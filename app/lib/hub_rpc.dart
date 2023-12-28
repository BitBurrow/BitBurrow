import 'dart:async';
import 'dart:math' as math;
import 'package:stream_channel/stream_channel.dart' as sc;
import 'package:json_rpc_2/json_rpc_2.dart' as jsonrpc;
import 'package:logging/logging.dart';
import 'dart:convert' as convert;
import 'dart:typed_data';
import 'persistent_websocket.dart';
import 'main.dart';

final _log = Logger('hub_rpc');
var loginState = LoginState.instance;

class HubRpc {
  static String? _convId; // conversation ID, unique per hub conversation
  PersistentWebSocket? _hubMessages;
  jsonrpc.Client? _rpc; // docs: https://pub.dev/packages/json_rpc_2
  final _errController = StreamController<String>.broadcast();
  Stream<String> get err => _errController.stream; // status/error messages
  static HubRpc? _instance; // singleton--one conversation with hub for the app
  HubRpc._();

  static HubRpc get instance {
    _instance ??= HubRpc._();
    return _instance!;
  }

  Future sendRequest(String method, [parameters, int timeOut = 45]) async {
    await connect(); // make sure we're connected or connecting
    if (_rpc == null) {
      // should never happen
      throw PWUnrecoverableError("B98002 unexpected disconnect");
    }
    // docs: https://pub.dev/packages/json_rpc_2`
    return _rpc!
        .sendRequest(method, parameters)
        .timeout(Duration(seconds: timeOut));
    // pass exceptions on to caller
  }

  /// (Re)establish connection to hub using JSON-RPC over PersistentWebSocket
  Future<void> connect() async {
    if (_rpc != null) {
      if (_hubMessages == null) {
        throw PWUnrecoverableError("B91165 internal error");
      }
      if (loginState.hub == _hubMessages!.host) {
        return; // already connected to the correct host
      } else {
        _hubMessages!.abandonConnection();
        for (int ms = 0;; ms += 50) {
          if (_rpc == null) {
            // via `_rpc = null` in `pws.onError()` below
            _log.config("B66547 took $ms ms to abandon connection");
            break;
          }
          if (ms > 3000) {
            throw PWUnrecoverableError("B87836 internal error");
          }
          await Future.delayed(const Duration(milliseconds: 50));
        }
      }
    }
    var authAccount = ""; // can be any valid login key or coupon
    // FIXME: consider replacing logic below with most recent RCP call to 'create_manager' or 'list_servers'
    if (loginState.loginKeyVerified && loginState.loginKey.isNotEmpty) {
      authAccount = loginState.pureLoginKey;
    } else if (loginState.coupon.isNotEmpty) {
      authAccount = loginState.pureCoupon;
    } else {
      authAccount = loginState.pureLoginKey;
    }
    // get new _convId when app restarts or fatal error in conversation
    _convId = newConvId();
    var logId = _convId!.substring(_convId!.length - 4); // only for logging
    var uri = Uri(
        scheme: 'wss',
        host: loginState.hub,
        port: 8443,
        path: '/rpc1/$authAccount/$_convId');
    _log.info("B28388 connecting to $uri");
    _hubMessages = PersistentWebSocket(logId, Logger('pws'));
    // copy messages from PersistentWebSocket (recreated after errors)
    // to our singleton broadcast stream
    _hubMessages!.err.listen(
      (data) {
        _errController.sink.add(data);
      },
      onError: (err) {
        _errController.sink.addError(err);
      },
      cancelOnError: false,
    );
    // .connect() future doesn't complete until disconnect
    Future<void> pws = _hubMessages!.connect(uri);
    pws.onError((error, stacktrace) {
      _rpc = null; // force reconnect on next sendRequest()
      // ignore actual error because it was sent via _hubMessages.err()
    });
    var channel = sc.StreamChannel(
        // in-bound stream, convert bytes to String (JSON)
        _hubMessages!.stream
            .asyncMap((data) => convert.utf8.decode(List<int>.from(data))),
        // out-bound sink
        _hubMessages!.sink);
    _rpc = jsonrpc.Client(channel.cast<String>());
    unawaited(_rpc!.listen()); // tell jsonrpc to subscribe to input
  }
}

/// Create a UUID-like, 9-character ID, e.g. "R1QRfQArg".
String newConvId() {
  String convId = '';
  final random = math.Random();
  for (int b = 0; b < 3; b++) {
    final int r;
    if (b == 0) {
      r = DateTime.now().millisecondsSinceEpoch; // ~41 bits
    } else {
      r = random.nextInt(0x100000000); // 32 bits
    }
    var bytes = Uint8List(4)..buffer.asByteData().setUint32(0, r, Endian.big);
    // use url-safe encoding (-_ rather than +/); use substring(0, 6) for more bits
    convId = convId + convert.base64Url.encode(bytes).substring(3, 6);
  }
  return convId;
}
