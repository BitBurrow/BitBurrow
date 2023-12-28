// ignore_for_file: prefer_conditional_assignment

import 'dart:async';
import 'dart:collection';
import 'dart:convert' as convert;
import 'dart:math' as math;
import 'dart:typed_data';
import 'dart:io' as io;
import 'package:logging/logging.dart';
import 'package:mutex/mutex.dart';
import 'package:web_socket_channel/io.dart' as wsio;

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

const lkoccString = '__login_key_or_coupon_code__';

/// Concatinate Uint8Lists.
Uint8List cat(Uint8List part1, Uint8List part2) {
  var chunk = BytesBuilder();
  chunk.add(part1);
  chunk.add(part2);
  return (chunk.toBytes());
}

// Make binary data more readable for humans.
String printableHex(Uint8List chunk) {
  StringBuffer out = StringBuffer();
  StringBuffer quote = StringBuffer(); // quoted ascii text
  for (int item in chunk) {
    if (item >= 32 && item <= 126 && item != 39) {
      // printable character, but not single quote
      quote.write(String.fromCharCode(item));
    } else {
      // non-printable character
      if (quote.isNotEmpty) {
        if (quote.length <= 3) {
          // isolated short strings remain as hex
          out.write(quote
              .toString()
              .codeUnits
              .map((e) => e.toRadixString(16).padLeft(2, '0').toUpperCase())
              .join(' '));
          out.write(' ');
        } else {
          out.write("'${quote.toString()}' ");
        }
        quote.clear();
      }
      out.write('${item.toRadixString(16).padLeft(2, '0').toUpperCase()} ');
    }
  }
  if (quote.isNotEmpty) {
    out.write("'${quote.toString()}'");
  }
  return out.toString().trim();
}

printableHexTest() {
  var chunkTest = "1234\x0056789\x01\x02abcd\nefg\nhi\nhello\n\n"
      "hello\n\n\nshouldn't \\ backslash\xe2\x9c\x94 done\n";
  var chunkTestOut =
      "'1234' 00 '56789' 01 02 'abcd' 0A 65 66 67 0A 68 69 0A 'hello' 0A 0A "
      "'hello' 0A 0A 0A 'shouldn' 27 't \\ backslash' E2 9C 94 ' done' 0A";
  assert(printableHex(Uint8List.fromList(chunkTest.codeUnits)) == chunkTestOut);
}

/// Implement a never-resets 'try' count.
class RetryCounter {
  static int _count = 0;

  static int next() {
    _count += 1;
    return _count;
  }
}

/// Check if host has valid DNS. Return an error message or "".
Future<String> dnsCheck(String host) async {
  try {
    List<io.InternetAddress> addresses = await io.InternetAddress.lookup(host);
    if (addresses.isEmpty) {
      return "B58730 cannot find a valid address for $host; "
          "check that it is typed correctly";
    }
    return "";
  } on io.SocketException {
    return "B51526 $host seems to be an invalid name; "
        "check that it is typed correctly";
  }
}

/// Convert exception into error message. Throw PWUnrecoverableError if fatal.
Future<String> exceptionText(Object err, StackTrace? stacktrace, host) async {
  var e = err.toString().replaceAll(RegExp(r'\s+'), ' ');
  if (err is io.WebSocketException) {
    if (e.startsWith('WebSocketException: Connection to ') &&
        e.endsWith(' was not upgraded to websocket')) {
      // maybe 403 Forbidden
      throw PWUnrecoverableError("B66703 $lkoccString not found; "
          "make sure it was entered correctly");
    }
  } else if (err is io.SocketException) {
    e = e.replaceFirst(RegExp(r'^SocketException: '), '');
    if (e.startsWith('No route to host')) {
      return "B55714 unable to connect to $host; "
          "at the moment (try ${RetryCounter.next()}); retrying ...";
    } else if (e.startsWith('No address associated with hostname') ||
        e.startsWith('Failed host lookup')) {
      String exampleOrg = await dnsCheck('example.org');
      if (exampleOrg.isEmpty) {
        // DNS for example.org works, so problem is probably with host
        throw PWUnrecoverableError("B32177 cannot connect to $host; "
            "check that it is typed correctly or try again later");
      }
      return "B89962 your internet connection seems to not be working "
          "at the moment (try ${RetryCounter.next()}); retrying ...";
    } else if (e.startsWith('Connection refused')) {
      throw PWUnrecoverableError("B66702 cannot connect to $host; "
          "check that it is typed correctly or try again later");
    } else if (e.startsWith('Connection reset by peer')) {
      throw PWUnrecoverableError("B66704 there was a problem with "
          "the connection to $host; check that it is typed correctly "
          "or try again later");
    } else if (e.startsWith('HTTP connection timed out')) {
      throw PWUnrecoverableError("B66705 the connection timed out; "
          "check your internet connection and try again");
    }
  } else if (e.startsWith("HandshakeException: Handshake error in client")) {
    // TLS not available at the TCP port (e.g. http, ssh)
    throw PWUnrecoverableError("B76376 there was a problem making a secure "
        "connection to $host; check that it is typed correctly "
        "or try again later");
  }
  throw PWUnrecoverableError("B19891 unable to connect to "
      "$host: $e");
}

/// Test connection via TCP and TLS.
/// Return an error message or "". Throws PWUnrecoverableError if fatal.
Future<String> connectivityCheck(String host, int port) async {
  try {
    var socket = await io.SecureSocket.connect(host, port); // try TCP over TLS
    await socket.close();
    return "";
  } catch (err, stacktrace) {
    return await exceptionText(err, stacktrace, host);
  }
}

// Future<void> connectivityCheckTest(uri) async {
//   var toCheck = [
//     'onfjdaiewtt.com:990',
//     '${uri.host}:22',
//     '${uri.host}:990',
//     '${uri.host}:8000', // run on test hub: python3 -m http.server 8000
//     '${uri.host}:8443',
//     '${uri.host}:${uri.port}',
//     '::1:22',
//     '::1:990',
//     '::1:8000',
//     '::1:8443',
//     '::1:${uri.port}',
//     '127.0.0.1:22',
//     '127.0.0.1:990',
//     '127.0.0.1:8000',
//     '127.0.0.1:8443',
//     '127.0.0.1:${uri.port}',
//   ];
//   for (var hostPort in toCheck) {
//     try {
//       int lastColon = hostPort.lastIndexOf(':');
//       String host = hostPort.substring(0, lastColon);
//       int port = int.parse(hostPort.substring(lastColon + 1));
//       print("====================== trying $host:$port");
//       String errorMessage = await connectivityCheck(host, port);
//       errorMessage = errorMessage.isEmpty ? "okay" : errorMessage;
//       print("    $errorMessage");
//     } on PWUnrecoverableError catch (err) {
//       print("    PWUnrecoverableError: ${err.message}");
//     }
//   }
// }

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
  String logId;
  // convert Python logger: error→severe; warn→warning; info→info; debug→config
  final Logger _log;
  wsio.IOWebSocketChannel? _ws;
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
  final _errController = StreamController<String>();
  Stream<String> get err => _errController.stream; // status/error messages
  bool _ipi = false;

  PersistentWebSocket(this.logId, this._log) {
    _outController.stream.listen((message) {
      send(message);
    });
  }

  Future<void> connected(wsio.IOWebSocketChannel ws) async {
    if (connectLock.isLocked) {
      _log.warning("B30103 $logId waiting for current WebSocket to close");
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
      _log.severe("B99843 unknown exception $err");
      rethrow;
    } finally {
      await setOfflineMode();
    }
  }

  /// Attempt to connect. Return WebSocket upon connection.
  Future<wsio.IOWebSocketChannel> _reconnect(Uri uri) async {
    // loop until we connect or get fatal error
    while (true) {
      // https://github.com/dart-lang/web_socket_channel/issues/61#issuecomment-1127554042
      final httpClient = io.HttpClient();
      httpClient.connectionTimeout = const Duration(seconds: 20);
      try {
        return wsio.IOWebSocketChannel(await io.WebSocket.connect(
            uri.toString(),
            customClient: httpClient));
        // ¿replace above with this?: return await wsc.IOWebSocketChannel.connect(url, customClient: httpClient);
      } catch (err, stacktrace) {
        String error = await exceptionText(err, stacktrace, uri.host);
        if (error.isNotEmpty) {
          _errController.sink.add(error);
        }
      }
      await Future.delayed(const Duration(seconds: 5));
    }
  }

  Future<void> connect(Uri uri) async {
    if (connectLock.isLocked) {
      _log.warning("B18450 $logId waiting for current WebSocket to close");
    }
    try {
      await connectLock.protect(() async {
        // _url = url;
        // keep reconnecting
        while (true) {
          setOnlineMode(await _reconnect(uri));
          await listen();
        }
      });
    } on PWUnrecoverableError catch (err) {
      _errController.sink.addError(err); // convey message to UI
      _inController.sink.addError(err); // cause jsonrpc.sendRequest() to abort
      rethrow; // force `_rpc = null;` in HubRpc to force reconnection
    } catch (err, stacktrace) {
      _log.severe("B76104 unknown exception $err; \n"
          "======= stacktrace:\n$stacktrace");
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
        _log.severe("B39284 $logId expected bytes, got string: $chunk");
        throw PWUnrecoverableError("B46517 server sent string, not bytes");
      }
      if (_log.level <= Level.CONFIG) {
        // call printableHex() only when needed
        _log.config("B18043 $logId received: ${printableHex(chunk)}");
      }
      var message = await processInbound(chunk);
      if (chaos > 0) {
        final random = math.Random();
        if (chaos > random.nextInt(1000)) {
          _log.warning(
              "B66741 $logId randomly closing WebSocket to test recovery");
          await Future.delayed(Duration(seconds: random.nextInt(3)));
          await setOfflineMode();
          await Future.delayed(Duration(seconds: random.nextInt(3)));
        }
      }
      if (message != null) {
        _inController.sink.add(message);
      }
    }
    _log.info("B39888 $logId WebSocket closed");
    await setOfflineMode();
  }

  Future<void> send(message) async {
    var flowControlDelay = 1;
    while (_journal.length > maxSendBuffer) {
      if (flowControlDelay == 1) {
        _log.info("B60015 $logId outbound buffer is full--waiting");
      }
      await Future.delayed(Duration(seconds: flowControlDelay));
      if (flowControlDelay < 30) {
        flowControlDelay += 1;
      }
    }
    if (flowControlDelay > 1) {
      _log.config("B60016 $logId resuming send");
    }
    Uint8List chunk;
    if (message is String) {
      // convert String to Uint8List
      chunk = cat(
          lsb(_journalIndex), Uint8List.fromList(convert.utf8.encode(message)));
    } else if (message is Uint8List) {
      chunk = cat(lsb(_journalIndex), message);
    } else {
      _log.info("B64474 unsupported type");
      chunk = Uint8List(0);
    }
    _journalIndex++;
    _journal.add(chunk);
    _sendRaw(chunk);
    enableJournalTimer();
    if (chaos > 0) {
      final random = math.Random();
      if (chaos > random.nextInt(1000)) {
        _log.warning(
            "B14264 $logId randomly closing WebSocket to test recovery");
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
      _log.severe("B38395 $logId remote wants journal[$startIndex:$endIndex] "
          "but we only have journal[$tailIndex:$_journalIndex]");
      _sendRaw(constOtw(_sigResendError));
      throw PWUnrecoverableError("B34923 $logId impossible resend request");
    }
    _log.info("B57685 $logId resending journal[$startIndex:$endIndex]");
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

  /// Send chunk of bytes if we can.
  void _sendRaw(Uint8List chunk) {
    if (isOffline()) {
      return;
    }
    _ws!.sink.add(chunk);
    if (_log.level <= Level.CONFIG) {
      // call printableHex() only when needed
      _log.config("B41790 $logId sent: ${printableHex(chunk)}");
    }
  }

  /// Test and respond to chunk, returning a message or null.
  Future<Uint8List?> processInbound(Uint8List chunk) async {
    if (_ipi == true) {
      _log.severe("B14726 $logId processInbound is not reentrant");
      await Future.delayed(
          const Duration(seconds: 1)); // avoid uninterruptible loop
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
        _log.info("B73823 $logId ignoring duplicate chunk $index");
      }
    } else {
      // signal
      if (iLsb == _sigAck || iLsb == _sigResend) {
        var ackIndex = unmod(bytesToInt(chunk, 2), _journalIndex);
        var tailIndex = _journalIndex - _journal.length;
        if (tailIndex < ackIndex) {
          _log.info("B60967 $logId clearing journal[$tailIndex:$ackIndex]");
        }
        if (_journalTimer != null) {
          _journalTimer!.cancel();
          _journalTimer = null;
        }
        if (_journal.length < (ackIndex - tailIndex)) {
          _log.severe("B19145 $logId error: "
              "${_journal.length} < ($ackIndex - $tailIndex)");
          throw PWUnrecoverableError("B44312 $logId impossible ack");
        }
        for (var i = tailIndex; i < ackIndex; i++) {
          _journal.removeFirst();
        }
        enableJournalTimer();
        if (iLsb == _sigResend) {
          await _resend(ackIndex);
        }
      } else if (iLsb == _sigResendError) {
        _log.severe("B75562 $logId received resend error signal");
        throw PWUnrecoverableError(
            "B91222 $logId received resend error signal");
      } else if (iLsb == _sigPing) {
        _sendRaw(cat(constOtw(_sigPong), chunk.sublist(2)));
      } else if (iLsb == _sigPong) {
        // pass
      } else {
        _log.severe("B32406 $logId unknown signal $iLsb");
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
      _log.info("B89446 $logId WebSocket closed");
    } on PWUnrecoverableError {
      rethrow;
    } catch (e) {
      _log.severe("B39426 $logId wsexception, ${e.toString().trim()}");
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
      throw PWUnrecoverableError("B65752 $logId cannot go online twice");
    }
    _ws = ws;
    _log.info("B17184 $logId WebSocket reconnect $connects");
    connects++;
    enableJournalTimer();
    enableInTimer();
  }

  void enableJournalTimer() {
    if (isOffline()) {
      return;
    }
    if (_journal.isNotEmpty && _journalTimer == null) {
      _journalTimer = Timer.periodic(const Duration(seconds: 2), _resendOne);
    }
  }

  void enableInTimer() {
    if (isOffline()) {
      return;
    }
    if (_inIndex > _inLastAck && _inLastAckTimer == null) {
      _inLastAckTimer = Timer(const Duration(seconds: 1), _sendAck);
    }
  }
}
