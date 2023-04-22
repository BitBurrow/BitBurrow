import 'dart:async';
import 'dart:io' as io;
// ignore: depend_on_referenced_packages
import 'package:web_socket_channel/io.dart' as wsc;

class PersistentWebSocket {
  late final String _url;
  // all constants are in seconds; be careful of subtle interactions
  static const connectRetry = 15; // wait after failed connect attempt
  static const reconnectDelay = 25; // wait for pong response
  static const pongRetry = 3; // wait after no pong response before retrying
  static const pingTime = 10; // send ping every n seconds
  static const connectionTimeout = 20; // wait for WebSocket connect attempt
  static const timerStep = 5;
  var _sinceLastPong = -timerStep; // seconds since last response from server
  Timer? _pingTimer;
  Timer? _heartbeat;
  wsc.IOWebSocketChannel? _channel;
  final _controller = StreamController<String>();
  Stream<String> get stream => _controller.stream;

  void add(String s) {
    if (_channel != null) {
      _channel!.sink.add(s);
    }
    // FIXME: how are we preventing data loss (both directions)?
  }

  PersistentWebSocket(url) {
    _url = url;
    _heartbeat = Timer.periodic(Duration(seconds: timerStep), _timerTick);
    _timerTick(_heartbeat!); // begin connecting--don't wait for periodic timer
  }

  void close() {
    if (_heartbeat != null) {
      _heartbeat!.cancel();
      _heartbeat = null;
    }
    _rebootConnection();
    _controller.close();
  }

  void _rebootConnection({retryIn = connectRetry}) {
    if (_sinceLastPong >= 0) {
      // ignore retryIn if _sinceLastPong already negative (first caller wins)
      _sinceLastPong = -retryIn; // retry connection after given delay
    }
    if (_channel != null) {
      _channel!.sink.close(); // close old WebSocket if open
      _channel = null;
    }
    if (_pingTimer != null) {
      _pingTimer!.cancel(); // kill ping timer if running
      _pingTimer = null;
    }
  }

  void _timerTick(Timer t) {
    print("tick $_sinceLastPong");
    // STATE: waiting to reconnect
    if (_sinceLastPong < 0) {
      _sinceLastPong += timerStep;
      if (_sinceLastPong < 0) {
        return; // need to wait some more
      }
      _sinceLastPong = 0;
      // https://github.com/dart-lang/web_socket_channel/issues/61#issuecomment-1127554042
      final httpClient = io.HttpClient();
      httpClient.connectionTimeout = Duration(seconds: connectionTimeout);
      io.WebSocket.connect(_url, customClient: httpClient).then((ws) {
        _channel = wsc.IOWebSocketChannel(ws);
        if (_channel == null) {
          print("failed to connect 3548");
          _rebootConnection();
        }
        _sinceLastPong = 1; // start pong timer
        print('connected');
        _pingTimer = Timer.periodic(const Duration(seconds: pingTime), (timer) {
          print('ping');
          _channel!.sink.add('ping');
        });
        _channel!.stream.listen(
          (data) {
            if (data == 'pong') {
              print('pong');
              _sinceLastPong = 1; // reset pong timer
            } else {
              _controller.sink.add(data);
            }
          },
          onError: (err) {
            print('error 243980');
            _rebootConnection();
          },
          // cancelOnError: true, // defaults to true
          onDone: () {
            print('error 239032 someone closed the connection');
            _rebootConnection();
          },
        );
      }).onError((error, stackTrace) {
        print("failed to connect 10667: $error");
        _rebootConnection();
      });
    }
    // STATE: connecting
    if (_sinceLastPong == 0) {
      return; // still attempting to connect
    }
    // STATE: connected
    if (_sinceLastPong > 0) {
      _sinceLastPong += timerStep;
      if (_sinceLastPong > reconnectDelay) {
        print('server is no longer responding');
        _rebootConnection(retryIn: pongRetry);
      }
    }
  } // _timerTick()
}

void main(List<String> arguments) async {
  print('one');
  final url = arguments[0];
  var ws = PersistentWebSocket(url);
  ws.stream.listen(
    (data) {
      print("data received: $data");
    },
  );
  print('two');
  await Future.delayed(Duration(seconds: 180));
  print('three');
  ws.close();
  print('four');
}
