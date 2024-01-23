//
// To run this file:
//   cd libs/dart/persistent_websocket/
//   dart create test_client
//   cd test_client/
//   ln -sf ../../test_client.dart bin/test_client.dart
//   ln -s ../../persistent_websocket.dart bin/persistent_websocket.dart
//   dart pub add web_socket_channel mutex
//   dart run test_client 'wss://vxm.example.org:8443/v1/pw/55'

import 'dart:async';
// import 'dart:collection';
import 'dart:io' as io;
import 'dart:convert';
import 'dart:typed_data';
import 'persistent_websocket.dart';

void main(List<String> arguments) async {
  final url = arguments[0];
  var pws = PersistentWebSocket("");
  pws.connect(url).onError((err, stackTrace) {
    print("B38925 error: $err");
    io.exit(1);
  });
  // (TESTING) pws.chaos = 50;
  pws.stream.listen(
    (data) {
      print("data received: $data");
    },
  );
  var toSend = 26000000;
  while (true) {
    await Future.delayed(Duration(milliseconds: 100));
    print("sending: $toSend");
    await pws.send(Uint8List.fromList(utf8.encode(toSend.toString())));
    toSend += 1;
  }
}
