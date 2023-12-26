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
  final _hubMessages = PersistentWebSocket('null', Logger('pws'));
  jsonrpc.Client? _rpc; // docs: https://pub.dev/packages/json_rpc_2
  Stream<String> get err => _hubMessages.err; // status/error messages
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

  /// Establish connection to hub using JSON-RPC over PersistentWebSocket
  Future<void> connect() async {
    if (_rpc != null) {
      return;
    }
    var authAccount = ""; // can be any valid login key or coupon
    if (loginState.loginKeyVerified && loginState.loginKey.isNotEmpty) {
      authAccount = loginState.pureLoginKey;
    } else if (loginState.coupon.isNotEmpty) {
      authAccount = loginState.pureCoupon;
    } else {
      authAccount = loginState.pureLoginKey;
    }
    try {
      if (_convId == null) {
        // get new _convId when app restarts or fatal error in conversation
        _convId = newConvId();
        var logId = _convId!.substring(_convId!.length - 4); // only for logging
        _hubMessages.logId = logId;
      }
      var uri = Uri(
          scheme: 'wss',
          host: loginState.hub,
          port: 8443,
          path: '/rpc1/$authAccount/$_convId');
      _log.info("connecting to $uri");
      _hubMessages.connect(uri); // Future doesn't complete until disconnect
      var channel = sc.StreamChannel(
          // in-bound stream, convert bytes to String (JSON)
          _hubMessages.stream
              .asyncMap((data) => convert.utf8.decode(List<int>.from(data))),
          // out-bound sink
          _hubMessages.sink);
      _rpc = jsonrpc.Client(channel.cast<String>());
      unawaited(_rpc!.listen()); // tell jsonrpc to subscribe to input
    } on PWUnrecoverableError catch (err) {
      // use specific name for what failed
      throw Exception(err.message.replaceFirst(lkoccString,
          authAccount == loginState.pureCoupon ? "coupon code" : "login key"));
    }
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
