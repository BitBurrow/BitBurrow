import 'dart:io' as io;
import 'dart:async' as async;
import 'package:dartssh2/dartssh2.dart' as ssh;
import 'package:logging/logging.dart';

final _log = Logger('bb_proxy');

class BbProxy {
  var sshUser = '';
  var sshKey = ''; // contents, not file path
  var sshDomain = '';
  var sshPort = 22;
  ssh.SSHClient? _client;

  /// Establish ssh connection. Return "" on success or error message.
  Future<String> connect() async {
    var tries = 0;
    Stopwatch stopwatch = Stopwatch()..start();
    var lastError = "";
    while (true) {
      var error = await _connectOnce();
      if (error == "") {
        return ""; // success
      }
      _client = null;
      if (error == lastError) {
        return error; // same error twice in a row
      }
      lastError = error;
      tries += 1;
      if (tries >= 7) {
        if (stopwatch.elapsedMilliseconds < 2000) {
          return error; // 7 tries in 2 seconds--giving up;
        } else {
          tries = 0;
          stopwatch = Stopwatch()..start();
        }
      }
    }
  }

  Future<String> _connectOnce() async {
    try {
      final socket = await ssh.SSHSocket.connect(sshDomain, sshPort);
      _client = ssh.SSHClient(
        socket,
        username: sshUser,
        identities: ssh.SSHKeyPair.fromPem(sshKey),
      );
      if (_client == null) {
        return "B41802 SSHClient null trying $sshDomain:$sshPort";
      }
      await _client!.authenticated;
    } on io.SocketException catch (err) {
      return "B88675 can't connect to $sshDomain:$sshPort: $err";
    } on ssh.SSHAuthAbortError catch (err) {
      return "B31284 can't connect to $sshDomain:$sshPort: $err";
    } on ssh.SSHAuthFailError catch (err) {
      return "B61302 bad ssh key: $err";
    } on ssh.SSHStateError catch (err) {
      return "B88975 ssh connection failed: $err"; // e.g. server proc killed
    } catch (err) {
      return "B50513 can't connect to $sshDomain: $err";
    }
    return "";
  }

  /// Disconnect ssh. Return "" on success or error message.
  Future<String> disconnect() async {
    if (_client != null) {
      try {
        _client!.close();
        await _client!.done;
      } catch (err) {
        return "B40413 can't close connection to $sshDomain: $err";
      }
    }
    _client = null;
    return "";
  }

  /// Ssh-forward port from remote. Return "" on success or error message.
  Future<String> forwardPort({
    required int fromPort,
    String toAddress = '',
    required int toPort,
  }) async {
    try {
      if (_client == null) {
        return "B76402 cannot forward port--not yet connected";
      }
      final forward = await _client!.forwardRemote(port: fromPort);
      if (forward == null) {
        return "B35542 can't forward fromPort $fromPort";
      }
      // don't await for connections from hub
      _waitForConnections(forward, toAddress, toPort);
      return "";
    } catch (err) {
      return "B61661 error forwarding: $err";
    }
  }

  async.Future<String> _waitForConnections(forward, toAddress, toPort) async {
    await for (final connection in forward.connections) {
      try {
        // new connection to target for each connection from hub
        final socket = await io.Socket.connect(
          toAddress,
          toPort,
          timeout: const Duration(seconds: 20),
        );
        connection.stream.cast<List<int>>().pipe(socket);
        socket.pipe(connection.sink);
      } catch (err) {
        _log.warning("B58185 can't connect to $toAddress:$toPort: $err");
        connection.sink.close();
      }
    }
    return "";
  }
}
