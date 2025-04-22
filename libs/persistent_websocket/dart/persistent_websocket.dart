/// Class PersistentWebsocket adds to WebSockets auto-reconnect and auto-resend of lost messages.
///
/// This class adds to WebSockets (client and server) the ability to automatically reconnect,
/// including for IP address changes, as well as resending any messages which may have been
/// lost. To accomplish this, it uses a custom protocol which adds 2 bytes to the beginning
/// of each WebSocket message and uses signals for acknowledgement and resend requests.
library;

import 'dart:async';
import 'dart:collection';
import 'package:logging/logging.dart';
import 'dart:convert' as convert;
import 'dart:math' as math;
import 'dart:io' as io;
import 'package:mutex/mutex.dart';
import 'dart:typed_data';
import 'package:web_socket_channel/io.dart' as wsio;

//// notes:
//   * important: mirror changes in corresponding Dart code--search "bMjZmLdFv"
//   * important: for breaking changes, increment rpc_ver
//   * messages always arrive and in order; signals can be lost if WebSocket disconnects
//   * all bytes are in big-endian byte order; use: int.from_bytes(chunk[0:2], 'big')
//   * 'chunk' refers to a full WebSocket item, including the first 2 bytes
//   * 'message' refers to chunk[2:] of a chunk that is not a signal (details below)
//   * index → chunk number; first chunk sent is chunk 0
//   * i_lsb → index mod max_lsb, i.e. the 14 least-significant bits
//   * jet_bit → specify channel; 0=RPC channel; 1=jet channel for TCP and other streams
//   * ping and pong (below) are barely implemented because we rely on WebSocket keep-alives
//// on-the-wire format:
//   * chunk containing a message (chunk[0:2] in range 0..32767):
//       * chunk[0:2] bits 0..13 i_lsb
//       * chunk[0:2] bit 14     jet_bit
//       * chunk[0:2] bit 15     0
//       * chunk[2:]   message
//   * chunk containing a jet channel command (chunk[0:2] in range 49152..65535):
//       * chunk[0:2] bits 0..13 i_lsb
//       * chunk[0:2] bit 14     1
//       * chunk[0:2] bit 15     1
//       * chunk[2:]  command, e.g. 'forward_to 192.168.8.1:80' or 'disconnect'
//   * a signaling chunk (chunk[0:2] in range 32768..49151):
const _sigAck = 0x8010; // "I have received n total chunks"
//       * chunk[2:4]  i_lsb of next expected chunk
const _sigResend = 0x8011; // "Please resend chunk n and everything after it"
//       * chunk[2:4]  i_lsb of first chunk to resend
const _sigResendError = 0x8012; // "I cannot resend the requested chunks"
//       * chunk[2:]   (optional, ignored)
const _sigPing = 0x8020; // "Are you alive?"
//       * chunk[2:]   (optional)
const _sigPong = 0x8021; // "Yes, I am alive."
//       * chunk[2:]   chunk[2:] from corresponding ping

const maxLsb = 16384; // always 16384 (2**14) except for testing (tested 64, 32)
const lsbMask = 16383; // aka         0b0011111111111111
const jetBit = 16384; // bit 14,      0b0100000000000000
const signalBit = 32768; // bit 15,   0b1000000000000000
const jetCmd = 49152; // bits 14, 15, 0b1100000000000000
const maxSendBuffer = 100; // not sure what a reasonable number here would be
// assert(maxLsb > maxSendBuffer * 3); // avoid wrap-around

/// Convert index to on-the-wire format; see i_lsb description.

Uint8List lsb(index, {int setBitMask = 0}) {
  return Uint8List(2)
    ..buffer
        .asByteData()
        .setUint16(0, (index % maxLsb) | setBitMask, Endian.big);
}

/// Convert _sig constant to on-the-wire format.
Uint8List constOtw(int c) {
  assert(c >= signalBit);
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
/// The input w is the window size (must be even), i.e. the number of
/// possible values for xx.
int unmod(int xx, int xxxx, {int w = maxLsb}) {
  assert(xx < w);
  final splitp = (xxxx + w ~/ 2) % w; // split point
  return xx + xxxx + w ~/ 2 - splitp - (xx > splitp ? w : 0);
}

/// Call a method after a specified number of seconds.
///
/// Seconds can be fractional. Repeating timers are possible using periodic() or
/// exponential(). For example usage, see test_timekeeper() in "tests/" directory.
class Timekeeper {
  // based on https://stackoverflow.com/a/45430833
  late double _timeout;
  final void Function() _callback;
  bool _isPeriodic;
  double _scaling;
  final double _maxTimeout;
  Timer? _timer;

  Timekeeper(double timeout, this._callback,
      {bool isPeriodic = false, double scaling = 1.0, double maxTimeout = 30.0})
      : _timeout = timeout,
        _isPeriodic = isPeriodic,
        _scaling = scaling,
        _maxTimeout = maxTimeout {
    _startTimer();
  }

  factory Timekeeper.periodic(double timeout, void Function() callback) {
    return Timekeeper(timeout, callback,
        isPeriodic: true, scaling: 1.0, maxTimeout: double.infinity);
  }

  factory Timekeeper.exponential(double timeout, void Function() callback,
      double scaling, double maxTimeout) {
    return Timekeeper(timeout, callback,
        isPeriodic: true, scaling: scaling, maxTimeout: maxTimeout);
  }

  void _startTimer() {
    _timer = Timer(Duration(milliseconds: (_timeout * 1000).toInt()), _job);
  }

  Future _job() async {
    _callback();
    if (_isPeriodic) {
      _timeout *= _scaling;
      if (_timeout > _maxTimeout) {
        _timeout = _maxTimeout;
        _scaling = 1.0;
      }
      _startTimer();
    }
  }

  void cancel() {
    _isPeriodic = false;
    _timer?.cancel();
  }
}

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

/// Make binary data more readable for humans.
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
    } else if (e.startsWith('HTTP connection timed out')) {
      return "B66705 the connection timed out "
          "(try ${RetryCounter.next()}); retrying ...";
    } else if (e.startsWith('Connection refused')) {
      throw PWUnrecoverableError("B66702 cannot connect to $host; "
          "check that it is typed correctly or try again later");
    } else if (e.startsWith('Connection reset by peer')) {
      throw PWUnrecoverableError("B66704 there was a problem with "
          "the connection to $host; check that it is typed correctly "
          "or try again later");
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
/// See the top of this file for details.
class PersistentWebSocket {
  String logId;
  // convert Python logger: error→severe; warn→warning; info→info; debug→config
  final Logger _log;
  wsio.IOWebSocketChannel? _ws;
  String host = "";
  int _inIndex = 0;
  int _inLastAck = 0;
  Timekeeper? _inLastAckTimer;
  int _inLastResend = 0;
  var _inLastResendTime = DateTime.utc(1970, 1, 1);
  final Queue<Uint8List> _journal = Queue<Uint8List>();
  int _journalIndex = 0;
  Timekeeper? _journalTimer;
  int connects = 0;
  int chaos = 0;
  final connectLock = Mutex();
  late TcpConnector _tcpConnect;
  final _inController = StreamController<Uint8List>();
  Stream<Uint8List> get stream => _inController.stream; // in-bound (Uint8List)
  final _outController = StreamController();
  StreamSink get sink => _outController.sink; // out-bound (String or Uint8List)
  final _errController = StreamController<String>();
  Stream<String> get err => _errController.stream; // status/error messages
  final _jetInController = StreamController<Uint8List>();
  Stream<Uint8List> get jetStream => _jetInController.stream; // for jet channel
  final _jetOutController = StreamController();
  StreamSink get jetSink => _jetOutController.sink; // for jet channel
  var _maintainConnection = true; // flag--set to false in abandonConnection()
  bool _ipi = false;

  PersistentWebSocket(this.logId, this._log) {
    _tcpConnect = TcpConnector(this);
    _outController.stream.listen((message) {
      send(message);
    });
    _jetOutController.stream.listen((message) {
      jetSend(message);
    });
  }

  /// Handle a new inbound WebSocket connection, yield inbound messages.
  ///
  /// This is the primary API entry point for a WebSocket SERVER. Signals from
  /// the client will be appropriately handled and inbound messages will be
  /// returned to the caller via `yield`.
  Future<void> connected(wsio.IOWebSocketChannel ws) async {
    if (connectLock.isLocked) {
      _log.warning("B30103 $logId waiting for current WebSocket to close");
    }
    try {
      await connectLock.protect(() async {
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

  /// Begin a new outbound WebSocket connection.
  ///
  /// This is the primary API entry point for a WebSocket CLIENT. Signals from
  /// the server will be appropriately handled and inbound messages will be
  /// returned to the caller via _inController.sink. The WebSocket will
  /// reconnect as needed. Can throw PWUnrecoverableError.
  Future<void> connect(Uri uri) async {
    host = uri.host;
    if (connectLock.isLocked) {
      _log.warning("B18450 $logId waiting for current WebSocket to close");
    }
    try {
      // PersistentWebsocket is not reentrant; if we don't lock here, messages
      // can arrive out-of-order
      await connectLock.protect(() async {
        // keep reconnecting
        while (true) {
          setOnlineMode(await _reconnect(uri));
          await listen();
        }
      });
    } on PWUnrecoverableError catch (err) {
      if (_maintainConnection) {
        _errController.sink.addError(err); // convey message to UI
      }
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

  /// Accept chunks on the WebSocket connection and add messages to _controller.
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
    if (!_maintainConnection) {
      throw PWUnrecoverableError("B47162 abandoning connection");
    }
    _log.info("B39888 $logId WebSocket closed");
    await setOfflineMode();
  }

  /// Send a message to the remote when possible, resending if necessary.
  ///
  /// To send on jet channel, use a set_bit_mask of jet_bit.
  Future<void> send(message, {int setBitMask = 0}) async {
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
      // convert message to Uint8List
      chunk = cat(lsb(_journalIndex, setBitMask: setBitMask),
          Uint8List.fromList(convert.utf8.encode(message)));
    } else if (message is Uint8List) {
      chunk = cat(lsb(_journalIndex, setBitMask: setBitMask), message);
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

  /// Same as 'send()', but sends on the jet channel.
  Future<void> jetSend(message) async {
    await send(message, setBitMask: jetBit);
  }

  /// Resend the oldest chunk.
  void _resendOne() {
    var journalLen = _journal.length;
    if (journalLen > 0) {
      // sending all chunks now may cause congestion, and we should get a
      // _sig_resend upon reconnect anyhow
      var tailIndex = _journalIndex - _journal.length;
      _resend(tailIndex, endIndex: tailIndex + 1);
    }
  }

  /// Resend queued chunks.
  Future<void> _resend(int startIndex, {endIndex}) async {
    endIndex ??= _journalIndex;
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
    try {
      var iLsb = bytesToInt(chunk, 0); // first 2 bytes of chunk
      bool isJet = iLsb & jetBit != 0;
      if (iLsb < signalBit || iLsb >= jetCmd) {
        // message chunk or jet command
        var index = unmod(
          iLsb & lsbMask, // iLsb with jet bit cleared
          _inIndex,
        ); // expand 14 bits to full index
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
          if (isJet) {
            if (iLsb >= jetCmd) {
              // jet channel command
              await _processJetCommand(convert.utf8.decode(chunk.sublist(2)));
            } else {
              // jet channel data
              _jetInController.sink.add(chunk.sublist(2));
            }
          } else {
            return chunk.sublist(2); // message
          }
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
            _log.fine("B60967 $logId clearing journal[$tailIndex:$ackIndex]");
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
    } finally {
      _ipi = false;
    }
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

  Future<void> _processJetCommand(String cmd) async {
    var word = cmd;
    var parms = '';
    var index = cmd.indexOf(' ');
    if (index >= 0) {
      word = cmd.substring(0, index);
      parms = cmd.substring(index + 1);
    }
    if (word == 'forward_to') {
      var target = parseIpPort(parms);
      _log.fine("B79749 received forward_to command"
          "for ${target['host']} port ${target['port']}");
      // if we are a peer, open new TCP connection; else do nothing
      await _tcpConnect.openPeerConnection(target['host'], target['port']);
    } else if (word == "disconnect") {
      _log.fine("B09954 received disconnect command");
      _tcpConnect.close();
    } else {
      _log.warning("B88787 unknown jet command: $cmd");
    }
  }

  Future<void> _sendTcpConnect(ipAddress, port) async {
    await send('forward_to ${formatIpPort(ipAddress, port)}',
        setBitMask: jetCmd);
  }

  Future<void> _sendTcpDisconnect(ipAddress, port) async {
    await send('disconnect', setBitMask: jetCmd);
  }

  void abandonConnection() {
    if (_ws == null) {
      return;
    }
    _maintainConnection = false;
    setOfflineMode();
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
    } catch (err) {
      _log.severe("B39426 $logId wsexception, ${err.toString().trim()}");
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

  /// Set a timer to resend any unacknowledged outbound chunks
  void enableJournalTimer() {
    if (isOffline()) {
      return;
    }
    if (_journal.isNotEmpty && _journalTimer == null) {
      _journalTimer = Timekeeper.exponential(2.0, _resendOne, 2.0, 30.0);
    }
  }

  /// Set a timer to acknowledge receipt of received chunks
  void enableInTimer() {
    if (isOffline()) {
      return; // run timers only when online
    }
    if (_inIndex > _inLastAck && _inLastAckTimer == null) {
      _inLastAckTimer = Timekeeper(1.0, _sendAck);
    }
  }

  Future<void> execAndForwardTcp(execArgs, String hostIpAddress, int hostPort,
      String peerIpAddress, int peerPort) async {
    await _tcpConnect.execAndForwardTcp(
        execArgs, hostIpAddress, hostPort, peerIpAddress, peerPort);
  }

  void allowPortForwarding(bool allowed) {
    _tcpConnect.allowPortForwarding(allowed);
  }
}

/// Parse host:port string into host and port.
///
/// Returns Map with 'host': String and 'port': int pairs. IPv6 addresses must
/// be in square brackets."""
Map<String, dynamic> parseIpPort(String hostPortString, [int defaultPort = 0]) {
  if (hostPortString.startsWith('[')) {
    // IPv6
    int closeBracket = hostPortString.indexOf(']');
    var port = 0;
    if (hostPortString.length > closeBracket + 1) {
      // close bracket is not end of string
      assert(hostPortString[closeBracket + 1] == ':');
      port = int.parse(hostPortString.substring(closeBracket + 2));
    } else {
      port = defaultPort;
    }
    return {'host': hostPortString.substring(1, closeBracket), 'port': port};
  }
  int colon = hostPortString.indexOf(':');
  if (colon >= 0) {
    return {
      'host': hostPortString.substring(0, colon),
      'port': int.parse(hostPortString.substring(colon + 1))
    };
  } else {
    return {'host': hostPortString, 'port': defaultPort};
  }
}

/// Return host:port string version with [] around IPv6 addresses.
String formatIpPort(String host, int port) {
  return host.contains(':') ? '[$host]:$port' : '$host:$port';
}

/// Open a TCP connection and shuffle data between it and the jet channel.
///
/// Typical usage is for the 'host' end of a PersistentWebsocket connection to call
/// exec_and_forward_tcp() and the 'peer' end to call allow_port_forwarding().
class TcpConnector {
  late final PersistentWebSocket _pws;
  var _allowPortForwarding = false; // for security, denied by default
  final List<io.Socket> _connections = []; // to call write(), close()
  // True==to local TCP port that we opened; False==to remote machine
  final _toHost = false;
  io.Socket? _peerConnection;

  TcpConnector(this._pws);

  void allowPortForwarding(bool allowed) {
    _allowPortForwarding = allowed; // for security, denied by default
  }

  /// Set up port forwarding, similar to 'ssh -L', and run an external program.
  Future<void> execAndForwardTcp(List<String> execArgs, String hostIpAddress,
      int hostPort, String peerIpAddress, int peerPort) async {
    throw UnimplementedError;
  }

  /// Initiate TCP connection out. Link data both ways with the jet channel.
  Future<void> openPeerConnection(String ipAddress, int port) async {
    if (_connections.isNotEmpty) {
      return; // allow at most 1 connection because there is 1 jet channel
    }
    if (_allowPortForwarding && _toHost == false) {
      io.Socket? socket;
      try {
        socket = await io.Socket.connect(ipAddress, port,
            timeout: const Duration(seconds: 20));
        _connections.add(socket); // TCP connection established
        _pws._sendTcpConnect(ipAddress, port);
        // pipe data from _pws into TCP connection
        await _pws.jetStream.pipe(socket as StreamConsumer<Uint8List>);
        // pipe data from TCP connection back to _pws
        await socket.pipe(_pws.jetSink as StreamConsumer<Uint8List>);
      } on io.SocketException catch (err) {
        _pws._log.warning("B49644 error $err");
      } finally {
        if (socket != null) {
          _connections.remove(socket);
          socket.close();
          _pws.jetSink.close();
        }
      }
    } // do nothing if remote connections are not permitted or if we are not a peer
  }

  void write(Uint8List data) {
    if (_connections.isNotEmpty) {
      _connections[0].add(data);
    }
  }

  /// Close TCP connection (host or peer). For host, keep listening port open.
  void close() {
    if (_connections.isNotEmpty) {
      _pws._log.fine("B38090 closing TCP connection");
      _connections[0].close();
    }
    if (_toHost == false && _peerConnection != null) {
      // peer
      _pws._log.fine("B00096 closing TCP peer connection");
      _peerConnection!.close();
      _peerConnection = null;
    }
  }
}
