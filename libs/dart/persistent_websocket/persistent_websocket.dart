import 'dart:async';
import 'dart:collection';
import 'dart:typed_data';
import 'dart:io' as io;
import 'package:mutex/mutex.dart';
import 'package:web_socket_channel/io.dart' as wsc;

const maxLsb = 32768; // always 32768 (2**15) except for testing (tested 64, 32)
const maxSendBuffer = 100; // not sure what a reasonable number here would be
//assert(maxLsb > maxSendBuffer * 3); // avoid wrap-around

/// Convert index to on-the-wire format; see i_lsb description.
Uint8List lsb(int index) {
  return Uint8List(2)
    ..buffer.asByteData().setInt16(0, index % maxLsb, Endian.big);
}

/// Convert _sig constant to on-the-wire format.
Uint8List constOtw(int c) {
  assert(c >= 32768); // 0x8000
  assert(c <= 65535); // 0xFFFF
  return Uint8List(2)..buffer.asByteData().setInt16(0, c, Endian.big);
}

/// Convert the 2 bytes at data[offset] to an int
int bytesToInt(Uint8List data, offset) {
  var bytes = ByteData.view(data.buffer);
  return bytes.getInt16(offset, Endian.big);
}

/// Undelete upper bits of xx by assuming it's near xxxx.
///
/// Put another way: find n where n%w is xx and abs(xxxx-n) <= w/2. For
/// example, unmod(yy, yyyy_today, 100) will convert a 2-digit year yy to
/// a 4-digit year by assuming yy is within 50 years of the current year.
int unmod(int xx, int xxxx, {int w = maxLsb}) {
  assert(xx <
      w); // w is the window size (must be even), i.e. the number of possible values for xx
  final splitp = (xxxx + w ~/ 2) % w; // split point
  return xx + xxxx + w ~/ 2 - splitp - (xx > splitp ? w : 0);
}

// void unmodTest() {
//   final random = Random();
//   final windowSizes = [10, 100, 1000, 10000, 32768, 8322];
//   for (final win in windowSizes) {
//     for (var i = 0; i < 1000000; i++) {
//       final short = random.nextInt(win);
//       final long = random.nextInt(0xFFFFFF);
//       final n = unmod(short, long, w: win);
//       //print("unmod($short, $long, $win) == $n");
//       if (n % win == short) {
//         if ((long - n).abs() <= win ~/ 2) continue;
//       }
//       print("unmod failed");
//       exit(1);
//     }
//   }
// }

class PWUnrecoverableError implements Exception {
  final String message;
  PWUnrecoverableError(this.message);
}

/// concatinate Uint8Lists
Uint8List cat(Uint8List part1, Uint8List part2) {
  var chunk = BytesBuilder();
  chunk.add(part1);
  chunk.add(part2);
  return (chunk.toBytes());
}

/// Adds to WebSockets auto-reconnect and auto-resend of lost messages.
///
/// This class adds to WebSockets (client and server) the ability to automatically reconnect,
/// including for IP address changes, as well as resending any messages which may have been
/// lost. To accomplish this, it uses a custom protocol which adds 2 bytes to the beginning
/// of each WebSocket message and uses signals for acknowledgement and resend requests.
class PersistentWebSocket {
  // important: mirror changes in corresponding Python code--search "bMjZmLdFv"
  static const _sigAck = 0x8010;
  static const _sigResend = 0x8011;
  static const _sigResendError = 0x8012;
  static const _sigPing = 0x8020;
  static const _sigPong = 0x8021;
  final String id;
  wsc.IOWebSocketChannel? _ws;
  // String? _url;
  int _recvIndex = 0;
  int _recvLastAck = 0;
  Timer? _recvLastAckTimer;
  final Queue<Uint8List> _journal = Queue<Uint8List>();
  int _journalIndex = 0;
  int connects = 0;
  int chaos = 0;
  final connectLock = Mutex();
  final _controller = StreamController<Uint8List>();
  Stream<Uint8List> get stream => _controller.stream;
  bool _ipi = false;

  PersistentWebSocket(this.id);

  Future<void> connected(wsc.IOWebSocketChannel ws) async {
    if (connectLock.isLocked) {
      print("B30103 $id waiting for current WebSocket to close");
    }
    await connectLock.protect(() async {
      // _url = null;
      _ws = ws;
      print("B17184 $id WebSocket reconnect $connects");
      connects++;
      await listen();
    });
  }

  // attempt to connect; return true upon connect, false for fatal errors
  Future<bool> _reconnect(String url) async {
    while (true) {
      // loop until we connect
      // https://github.com/dart-lang/web_socket_channel/issues/61#issuecomment-1127554042
      final httpClient = io.HttpClient();
      httpClient.connectionTimeout = Duration(seconds: 20);
      await io.WebSocket.connect(url, customClient: httpClient).then((httpCon) {
        _ws = wsc.IOWebSocketChannel(httpCon);
      }).onError((error, stackTrace) {
        var e = error.toString();
        if (e.startsWith('SocketException: Connection refused')) {
          print("B66702 connection refused");
        } else if (e.startsWith('WebSocketException: Connection to ') &&
            e.endsWith(' was not upgraded to websocket')) {
          print("B66703 not upgraded to WebSocket");
        } else if (e.startsWith('SocketException: Connection reset by peer')) {
          print("B66704 connection reset by peer");
        } else if (e.startsWith('SocketException: HTTP connection timed out')) {
          print("B66705 connection timed out");
        } else {
          print("B66701 $e");
        }
        _ws = null;
      });
      if (_ws != null) {
        return true; // connected
      }
    }
  }

  Future<void> connect(String url) async {
    if (connectLock.isLocked) {
      print("B18450 $id waiting for current WebSocket to close");
    }
    await connectLock.protect(() async {
      // _url = url;
      // keep reconnecting
      while (await _reconnect(url)) {
        print("B35537 $id waiting for WebSocket to connect");
        connects++;
        await listen();
      }
    });
  }

  /// Accept chunks on the WebSocket connection and add messages to _controller
  Future listen() async {
    _sendRaw(cat(constOtw(_sigResend), lsb(_recvIndex)));
    await for (var chunk in _ws!.stream) {
      // if (chaos > 0 && chaos > Random().nextInt(1000)) {
      //   print("B66741 $id randomly closing WebSocket to test recovery");
      //   await Future.delayed(Duration(seconds: Random().nextInt(3)));
      //   await ensureClosed();
      //   await Future.delayed(Duration(seconds: Random().nextInt(3)));
      // }
      var message = await processInbound(chunk);
      if (message != null) {
        _controller.sink.add(message);
      }
    }
    print("B39654 $id WebSocket closed");
  }

  Future<void> send(Uint8List message) async {
    var flowControlDelay = 1;
    while (_journal.length > maxSendBuffer) {
      if (flowControlDelay == 1) {
        print("B60015 $id outbound buffer is full--waiting");
      }
      await Future.delayed(Duration(seconds: flowControlDelay));
      if (flowControlDelay < 30) {
        flowControlDelay += 1;
      }
    }
    if (flowControlDelay > 1) {
      print("B60016 $id resuming send");
    }
    Uint8List chunk = cat(lsb(_journalIndex), message);
    _journalIndex++;
    _journal.add(chunk);
    _sendRaw(chunk);
    // if (chaos > 0 && chaos > Random().nextInt(1000)) {
    //   print("B14264 $id randomly closing WebSocket to test recovery");
    //   await Future.delayed(Duration(seconds: Random().nextInt(3)));
    //   await ensureClosed();
    //   await Future.delayed(Duration(seconds: Random().nextInt(3)));
    // }
  }

  /// Resend queed chunks.
  Future<void> _resend(int startIndex) async {
    if (startIndex == _journalIndex) {
      return;
    }
    var tailIndex = _journalIndex - _journal.length;
    if (_journalIndex < startIndex || startIndex < tailIndex) {
      print("B38395 $id remote wants journal[$startIndex:] "
          "but we only have journal[$tailIndex:$_journalIndex]");
      _sendRaw(constOtw(_sigResendError));
      throw PWUnrecoverableError("B34923 $id impossible resend request");
    }
    print("B57685 $id resending journal[$startIndex:$_journalIndex]");
    // send requested chunks from oldest to newest
    var start = startIndex - _journalIndex;
    var i = 0 - _journal.length;
    for (var chunk in _journal) {
      if (i >= start) {
        _sendRaw(chunk);
      }
      i++;
    }
  }

  /// Send chunk of bytes of we can.
  void _sendRaw(Uint8List chunk) {
    if (_ws == null) {
      return;
    }
    _ws!.sink.add(chunk);
    print("B41790 $id sent: ${chunk.join(' ')}");
  }

  /// Test and respond to chunk, returning a message or null.
  Future<Uint8List?> processInbound(Uint8List chunk) async {
    if (_ipi == true) {
      print("B14726 $id processInbound is not reentrant");
      await Future.delayed(Duration(seconds: 1)); // avoid uninterruptible loop
      return null;
    }
    _ipi = true;
    var iLsb = bytesToInt(chunk, 0); // first 2 bytes of chunk
    if (iLsb < maxLsb) {
      // message chunk
      var index = unmod(iLsb, _recvIndex); // expand 15 bits to full index
      if (index == _recvIndex) {
        // valid
        _recvIndex++; // have unacknowledged message(s)
        // occasionally call _send_ack() so remote can clear _journal
        // ignore: prefer_conditional_assignment
        if (_recvLastAckTimer == null) {
          // acknowledge receipt after 1 second
          _recvLastAckTimer = Timer(Duration(seconds: 1), _sendAck);
        }
        if (_recvIndex - _recvLastAck >= 16) {
          // acknowledge receipt after 16 messages
          _sendAck();
        }
        _ipi = false;
        return chunk.sublist(2); // message
      } else if (index > _recvIndex) {
        // request the other end resend what we're missing
        _sendRaw(cat(constOtw(_sigResend), lsb(_recvIndex)));
      }
      print("B73823 $id ignoring duplicate chunk $index");
    } else {
      // signal
      if (iLsb == _sigAck) {
        var ackIndex = unmod(bytesToInt(chunk, 2), _journalIndex);
        var tailIndex = _journalIndex - _journal.length;
        print("B60967 $id clearing journal[$tailIndex:$ackIndex]");
        for (var i = tailIndex; i < ackIndex; i++) {
          _journal.removeFirst();
        }
      } else if (iLsb == _sigResend) {
        var resendIndex = unmod(bytesToInt(chunk, 2), _journalIndex);
        await _resend(resendIndex);
      } else if (iLsb == _sigResendError) {
        print("B75562 $id received resend error signal");
        await ensureClosed();
        throw PWUnrecoverableError("B91222 $id received resend error signal");
      } else if (iLsb == _sigPing) {
        _sendRaw(cat(constOtw(_sigPong), chunk.sublist(2)));
      } else if (iLsb == _sigPong) {
        // pass
      } else {
        print("B32406 $id unknown signal $iLsb");
      }
    }
    _ipi = false;
    return null;
  }

  void _sendAck() {
    _recvLastAck = _recvIndex;
    _recvLastAckTimer = null;
    _sendRaw(cat(constOtw(_sigAck), lsb(_recvIndex)));
  }

  Future<void> ping(Uint8List data) async {
    _sendRaw(cat(constOtw(_sigPing), data));
  }

  /// Close the WebSocket connection; can be called multiple times.
  Future<void> ensureClosed() async {
    if (_ws == null) {
      return;
    }
    try {
      await _ws!.sink.close();
      print("B89446 $id WebSocket closed");
    } on PWUnrecoverableError {
      rethrow;
    } catch (e) {
      print("B39426 $id wsexception, ${e.toString().trim()}");
    }
    _ws = null;
  }
}
