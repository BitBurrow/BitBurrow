// ignore_for_file: prefer_conditional_assignment

import 'dart:async';
import 'dart:collection';
import 'dart:convert' as convert;
import 'dart:math';
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
    ..buffer.asByteData().setUint16(0, index % maxLsb, Endian.big);
}

/// Convert _sig constant to on-the-wire format.
Uint8List constOtw(int c) {
  assert(c >= 32768); // 0x8000
  assert(c <= 65535); // 0xFFFF
  return Uint8List(2)..buffer.asByteData().setUint16(0, c, Endian.big);
}

/// Convert the 2 bytes at data[offset] to an int
int bytesToInt(Uint8List data, offset) {
  var bytes = ByteData.view(data.buffer);
  return bytes.getUint16(offset, Endian.big);
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
  int _inIndex = 0;
  int _inLastAck = 0;
  Timer? _inLastAckTimer;
  int _inLastResend = 0;
  var _inLastResendTime = DateTime.utc(1970, 1, 1);
  final Queue<Uint8List> _journal = Queue<Uint8List>();
  int _journalIndex = 0;
  Timer? _journalTimer;
  int connects = 0;
  int chaos = 0;
  final connectLock = Mutex();
  final _inController = StreamController<Uint8List>();
  Stream<Uint8List> get stream => _inController.stream; // in-bound (Uint8List)
  final _outController = StreamController();
  StreamSink get sink => _outController.sink; // out-bound (String or Uint8List)
  bool _ipi = false;

  PersistentWebSocket(this.id) {
    _outController.stream.listen((message) {
      send(message);
    });
  }

  Future<void> connected(wsc.IOWebSocketChannel ws) async {
    if (connectLock.isLocked) {
      print("B30103 $id waiting for current WebSocket to close");
    }
    try {
      await connectLock.protect(() async {
        // _url = null;
        setOnlineMode(ws);
        await listen();
      });
    } on PWUnrecoverableError {
      rethrow;
    } catch (err) {
      print("B99843 unknown exception $err");
      rethrow;
    } finally {
      await setOfflineMode();
    }
  }

  // attempt to connect; return WebSocket upon connect, exception for fatal errors
  Future<wsc.IOWebSocketChannel> _reconnect(String url) async {
    while (true) {
      // loop until we connect or get fatal error
      // https://github.com/dart-lang/web_socket_channel/issues/61#issuecomment-1127554042
      final httpClient = io.HttpClient();
      httpClient.connectionTimeout = Duration(seconds: 20);
      try {
        return wsc.IOWebSocketChannel(
            await io.WebSocket.connect(url, customClient: httpClient));
        // ¿replace above with this?: return await wsc.IOWebSocketChannel.connect(url, customClient: httpClient);
      } on PWUnrecoverableError {
        rethrow;
      } on io.WebSocketException catch (err) {
        var e = err.toString();
        if (e.startsWith('WebSocketException: Connection to ') &&
            e.endsWith(' was not upgraded to websocket')) {
          print("B66703 not upgraded to WebSocket"); // maybe 403 Forbidden
        } else {
          print("B44148 WebSocketException $e");
        }
      } on io.SocketException catch (err) {
        var e = err.toString();
        if (e.startsWith('SocketException: Connection refused')) {
          print("B66702 connection refused");
        } else if (e.startsWith('SocketException: Connection reset by peer')) {
          print("B66704 connection reset by peer");
        } else if (e.startsWith('SocketException: HTTP connection timed out')) {
          print("B66705 connection timed out");
        } else if (e.startsWith('SocketException: No route to host')) {
          print("B55714 no route to host");
        } else {
          print("B19891 SocketException $e");
        }
      } catch (err) {
        print("B66701 $e");
        rethrow;
      }
      await Future.delayed(Duration(seconds: 5));
    }
  }

  Future<void> connect(String url) async {
    if (connectLock.isLocked) {
      print("B18450 $id waiting for current WebSocket to close");
    }
    try {
      await connectLock.protect(() async {
        // _url = url;
        // keep reconnecting
        while (true) {
          setOnlineMode(await _reconnect(url));
          await listen();
        }
      });
    } on PWUnrecoverableError {
      rethrow;
    } catch (err, stackTrace) {
      print("B76104 unknown exception $err; \nstacktrace:\n$stackTrace");
      rethrow;
    } finally {
      await setOfflineMode();
    }
  }

  /// Accept chunks on the WebSocket connection and add messages to _controller
  Future listen() async {
    _inLastResendTime = DateTime.utc(1970, 1, 1); // reset for new connection
    _sendResend(); // chunks were probably lost in the reconnect
    enableJournalTimer();
    await for (var chunk in _ws!.stream) {
      if (chunk is String) {
        print("B39284 $id expected bytes, got string: $chunk");
        throw PWUnrecoverableError("B46517 server sent string, not bytes");
      }
      var hex = chunk.map((e) => e.toRadixString(16)).join(' ').toUpperCase();
      print("B18043 $id received: $hex");
      var message = await processInbound(chunk);
      if (chaos > 0) {
        final random = Random();
        if (chaos > random.nextInt(1000)) {
          print("B66741 $id randomly closing WebSocket to test recovery");
          await Future.delayed(Duration(seconds: random.nextInt(3)));
          await setOfflineMode();
          await Future.delayed(Duration(seconds: random.nextInt(3)));
        }
      }
      if (message != null) {
        _inController.sink.add(message);
      }
    }
    print("B39654 $id WebSocket closed");
    await setOfflineMode();
  }

  Future<void> send(message) async {
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
    Uint8List chunk;
    if (message is String) {
      // convert String to Uint8List
      chunk = cat(
          lsb(_journalIndex), Uint8List.fromList(convert.utf8.encode(message)));
    } else if (message is Uint8List) {
      chunk = cat(lsb(_journalIndex), message);
    } else {
      print("B64474 unsupported type");
      chunk = Uint8List(0);
    }
    _journalIndex++;
    _journal.add(chunk);
    _sendRaw(chunk);
    enableJournalTimer();
    if (chaos > 0) {
      final random = Random();
      if (chaos > random.nextInt(1000)) {
        print("B14264 $id randomly closing WebSocket to test recovery");
        await Future.delayed(Duration(seconds: random.nextInt(3)));
        await setOfflineMode();
        await Future.delayed(Duration(seconds: random.nextInt(3)));
      }
    }
  }

  void _resendOne(var timer) {
    var journalLen = _journal.length;
    if (journalLen > 0) {
      var tailIndex = _journalIndex - _journal.length;
      _resend(tailIndex, endIndex: tailIndex + 1);
    }
  }

  /// Resend queed chunks.
  Future<void> _resend(int startIndex, {endIndex}) async {
    if (endIndex == null) {
      endIndex = _journalIndex;
    }
    if (startIndex == endIndex) {
      return;
    }
    var tailIndex = _journalIndex - _journal.length;
    if (endIndex < startIndex || startIndex < tailIndex) {
      print("B38395 $id remote wants journal[$startIndex:$endIndex] "
          "but we only have journal[$tailIndex:$_journalIndex]");
      _sendRaw(constOtw(_sigResendError));
      throw PWUnrecoverableError("B34923 $id impossible resend request");
    }
    print("B57685 $id resending journal[$startIndex:$endIndex]");
    // send requested chunks from oldest to newest
    var start = startIndex - _journalIndex;
    var i = 0 - _journal.length;
    var end = endIndex - _journalIndex;
    for (var chunk in _journal) {
      if (i >= start && i < end) {
        _sendRaw(chunk);
      }
      i++;
    }
  }

  /// Send chunk of bytes of we can.
  void _sendRaw(Uint8List chunk) {
    if (isOffline()) {
      return;
    }
    _ws!.sink.add(chunk);
    var hex = chunk.map((e) => e.toRadixString(16)).join(' ').toUpperCase();
    print("B41790 $id sent: $hex");
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
      var index = unmod(iLsb, _inIndex); // expand 15 bits to full index
      if (index == _inIndex) {
        // valid
        _inIndex++; // have unacknowledged message(s)
        // occasionally call _send_ack() so remote can clear _journal
        enableInTimer();
        if (_inIndex - _inLastAck >= 16) {
          // acknowledge receipt after 16 messages
          _sendAck();
          // (TESTING) import 'dart:convert' show utf8;
          // (TESTING) send(Uint8List.fromList(
          // (TESTING)     utf8.encode("We've received $_inIndex messages"))); // TESTING
        }
        _ipi = false;
        return chunk.sublist(2); // message
      } else if (index > _inIndex) {
        _sendResend(); // request the other end resend what we're missing
      } else {
        // index < _inIndex
        print("B73823 $id ignoring duplicate chunk $index");
      }
    } else {
      // signal
      if (iLsb == _sigAck || iLsb == _sigResend) {
        var ackIndex = unmod(bytesToInt(chunk, 2), _journalIndex);
        var tailIndex = _journalIndex - _journal.length;
        if (tailIndex < ackIndex) {
          print("B60967 $id clearing journal[$tailIndex:$ackIndex]");
        }
        if (_journalTimer != null) {
          _journalTimer!.cancel();
          _journalTimer = null;
        }
        if (_journal.length < (ackIndex - tailIndex)) {
          print("B19145 $id error: "
              "${_journal.length} < ($ackIndex - $tailIndex)");
          throw PWUnrecoverableError("B44312 $id impossible ack");
        }
        for (var i = tailIndex; i < ackIndex; i++) {
          _journal.removeFirst();
        }
        enableJournalTimer();
        if (iLsb == _sigResend) {
          await _resend(ackIndex);
        }
      } else if (iLsb == _sigResendError) {
        print("B75562 $id received resend error signal");
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
    _inLastAck = _inIndex;
    if (_inLastAckTimer != null) {
      _inLastAckTimer!.cancel();
      _inLastAckTimer = null;
    }
    _sendRaw(cat(constOtw(_sigAck), lsb(_inIndex)));
  }

  void _sendResend() {
    var nowTime = DateTime.now();
    // wait a bit before sending a duplicate resend requets again
    if (_inIndex == _inLastResend) {
      var ms = nowTime.difference(_inLastResendTime).inMilliseconds;
      if (ms < 500) {
        return;
      }
    }
    _inLastResend = _inIndex;
    _inLastResendTime = nowTime;
    _sendRaw(cat(constOtw(_sigResend), lsb(_inIndex)));
  }

  Future<void> ping(Uint8List data) async {
    _sendRaw(cat(constOtw(_sigPing), data));
  }

  bool isOnline() => _ws != null;

  bool isOffline() => _ws == null;

  /// Close the WebSocket connection; can be called multiple times.
  Future<void> setOfflineMode() async {
    if (isOffline()) {
      return;
    }
    try {
      await _ws!.sink.close();
      print("B89446 $id WebSocket closed");
    } on PWUnrecoverableError {
      rethrow;
    } catch (e) {
      print("B39426 $id wsexception, ${e.toString().trim()}");
    } finally {
      _ws = null;
      if (_journalTimer != null) {
        _journalTimer!.cancel();
        _journalTimer = null;
      }
      if (_inLastAckTimer != null) {
        _inLastAckTimer!.cancel();
        _inLastAckTimer = null;
      }
    }
  }

  Future<void> setOnlineMode(ws) async {
    if (isOnline()) {
      throw PWUnrecoverableError("B65752 $id cannot go online twice");
    }
    _ws = ws;
    print("B17184 $id WebSocket reconnect $connects");
    connects++;
    enableJournalTimer();
    enableInTimer();
  }

  void enableJournalTimer() {
    if (isOffline()) {
      return;
    }
    if (_journal.isNotEmpty && _journalTimer == null) {
      _journalTimer = Timer.periodic(Duration(seconds: 2), _resendOne);
    }
  }

  void enableInTimer() {
    if (isOffline()) {
      return;
    }
    if (_inIndex > _inLastAck && _inLastAckTimer == null) {
      _inLastAckTimer = Timer(Duration(seconds: 1), _sendAck);
    }
  }
}
