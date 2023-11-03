import 'dart:async' as dasync;
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

enum States {
  notConnected,
  connectionFailed, // not worth retrying unless hub or authAccount change
  connecting,
  connected, // WebSocket may be attempting to reconnect
}

class HubRpc {
  var _state = States.notConnected;
  static String? _convId; // conversation ID, unique per hub conversation
  static PersistentWebSocket? _hubMessages;
  jsonrpc.Peer? _rpc;
  static HubRpc? _instance; // singleton--one conversation with hub for the app
  HubRpc._();

  static HubRpc get instance {
    _instance ??= HubRpc._();
    return _instance!;
  }

  void sendRequest(String method, [parameters]) {
    connect(); // make sure we're connected
    _rpc!.sendRequest(method, parameters);
  }

  Future<void> connect() async {
    if (_state == States.connecting || _state == States.connected) return;
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
        _hubMessages = PersistentWebSocket(logId, Logger('pws'));
      }
      var url = Uri(
              scheme: 'wss',
              host: loginState.hub,
              port: 8443,
              path: '/rpc1/$authAccount/$_convId')
          .toString();
      _log.info("connecting to $url");
      _hubMessages!.connect(url).onError((err, stackTrace) {
        _log.warning("B17834 pws: $err");
      });
      var channel = sc.StreamChannel(
          // in-bound stream, convert bytes to String (JSON)
          _hubMessages!.stream
              .asyncMap((data) => convert.utf8.decode(List<int>.from(data))),
          // out-bound sink
          _hubMessages!.sink);
      _rpc = jsonrpc.Peer(channel.cast<String>());
    } catch (err) {
      _log.warning("B40125 pws: $err");
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
