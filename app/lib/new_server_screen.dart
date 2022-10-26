// ignore_for_file: avoid_print

import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'package:flutter_svg/flutter_svg.dart';
import 'package:dartssh2/dartssh2.dart' as ssh;
import 'dart:io' as io;
import 'dart:async' as async;
import 'dart:convert' as convert;
import 'dart:typed_data';
import 'main.dart';
import 'json_chunks.dart';

const textBlob = """
All of these steps should be done at your "VPN home" location.

` `
## 1. Connect your new router to the internet.

* Make a note of the existing set-up in case there is a problem setting 
  up the new router.
* If possible, install the new router *in place of*  the existing one. 
  This will be more reliable in the long run, but it is generally only 
  possible if the existing set-up consists of a modem (DSL, ADSL, cable, 
  fiber, etc.) and a router, connected by an Ethernet cable. Disconnect 
  the Ethernet cable from the existing router and connect it to the WAN 
  jack on your new router. The WAN jack is sometimes labeled "Ethernet 
  In", "Internet", with a globe symbol, or is unlabeled but uniquely 
  colored. [More details.](/one-router-details)
* If you do not have the set-up described above, or you are unsure, 
  then use the Ethernet cable that came with your new router. Connect 
  one end to any of the unused LAN jacks on the existing router. 
  Connect the other end to the WAN jack on your new router. The LAN jacks 
  are sometimes labeled "Ethernet" or "Ethernet out" or simply numbered 
  1, 2, etc. The WAN jack is sometimes labeled "Ethernet In", 
  "Internet", with a globe symbol, or is unlabeled but uniquely colored. 
  [More details.](/two-routers-details)

` `
## 2. Plug your new router into a wall socket.

* Make sure at least one light turns on.
* It may take a few minutes for the WiFi to begin working.

` `
## 3. Connect to the new router via WiFi.
* It is sometimes necessary to turn off mobile data (internet via 
  your cellular provider).
* Enable WiFi if needed and scan for available WiFi networks.
* For the GL-AX1800, the WiFi name will be `GL-AX1800-xxx` or 
  `GL-AX1800-xxx-5G` and the WiFi password written on the bottom of 
  the router ("WiFi Key:").
""";

class NewServerScreen extends StatelessWidget {
  const NewServerScreen({Key? key}) : super(key: key);

  @override
  Widget build(BuildContext context) => ourScreenLayout(
        context,
        const NewServerForm(),
      );
}

class NewServerForm extends ParentForm {
  const NewServerForm({Key? key}) : super(key: key);

  @override
  NewServerFormState createState() => NewServerFormState();
}

class NewServerFormState extends ParentFormState {
  Map<String, dynamic>? sshLogin;
  async.Completer hubCommanderFinished = async.Completer();

  @override
  String get restorationId => 'new_server_form';

  @override
  Future<http.Response?> callApi() => http.post(Uri.http(
        '${loginState.hub}:8443',
        '/v1/accounts/${loginState.loginKey}/servers',
      ));

  @override
  String validateStatusCode(status) {
    if (status == 201) return "";
    if (status == 403) return "Invalid login key. Please sign in again.";
    return "The hub responseded with an invalid status code. "
        "Make sure you typed the hub correctly, try again later, or "
        "contact the hub administrator.";
  }

  @override
  String processApiResponse(response) {
    final jsonResponse =
        convert.jsonDecode(response.body) as Map<String, dynamic>;
    String? sshKey = jsonResponse['ssh_key'];
    int? sshPort = jsonResponse['ssh_port'];
    if (sshKey == null || sshPort == null) {
      return "invalid server response"; // error
    } else {
      sshLogin = jsonResponse;
      return "";
    }
  }

  @override
  nextScreen() => bbProxy();

  @override
  String getHubValue() => loginState.hub;

  @override
  void setHubValue(value) {
    loginState.hub = value;
  }

  @override
  String getAccountValue() => "";

  @override
  void setAccountValue(value) {
    loginState.loginKey = value;
  }

  Future bbProxy() async {
    var progressDialog = showSimpleDialog(
      context,
      "Configuring BitBurrow VPN server ...",
      '',
      "CANCEL",
    );
    var dialogState = DialogStates.open;
    progressDialog.whenComplete(() {
      if (dialogState == DialogStates.open) {
        print("user canceled dialog before request completed");
        dialogState = DialogStates.canceled;
      } else {
        dialogState = DialogStates.closed;
      }
    });
    var error = "";
    try {
      hubCommanderFinished = async.Completer();
      if (sshLogin != null) {
        await sshPortForward(
          sshUser: sshLogin!['ssh_user'],
          sshKey: sshLogin!['ssh_key'],
          sshDomain: sshLogin!['ssh_domain'],
          sshPort: sshLogin!['ssh_port'],
          sourcePort: sshLogin!['source_port'],
          destDomain: '192.168.8.1',
          // destPortList: [0, 22, 23, 80, 443, 8443],
          destPortList: [0],
        );
      } else {
        error = "B12944 sshLogin is null";
      }
    } catch (err) {
      error = err.toString();
    }
    if (dialogState == DialogStates.canceled) {
      print("(finished configuring but user canceled the progress dialog)");
      return;
    }
    if (!mounted) {
      print("B25601 finished configuring but !mounted");
      return;
    }
    dialogState = DialogStates.closing;
    Navigator.pop(context); // close dialog
    // var displayError = "";
    // if (error.isEmpty) {}
  }

  Future sshPortForward({
    sshUser,
    sshKey, // actual key contents
    sshDomain,
    sshPort = 22,
    sourcePort, // first of block of TCP ports on sshDomain
    destDomain,
    destPortList, // forward from sourcePort+2 to destDomain:destPortList[2]
  }) async {
    List<Future> forwardOneList = [];
    try {
      final socket = await ssh.SSHSocket.connect(sshDomain, sshPort);
      final client = ssh.SSHClient(
        socket,
        username: sshUser,
        identities: ssh.SSHKeyPair.fromPem(sshKey),
      );
      await client.authenticated;
      // forward ports from server to us
      List<Stream> forwardConnectionList = [];
      for (var i = 0; i < destPortList.length; i += 1) {
        final forward = await client.forwardRemote(port: sourcePort + i);
        if (forward == null) {
          print("B35541 can't forward $sshDomain:${sourcePort + i}");
        } else {
          // add to list: $sshDomain:${sourcePort + i}
          forwardConnectionList.add(forward.connections);
        }
      }
      // call forwardOne for each port to pipe to remote
      forwardConnectionList.asMap().forEach((index, c) {
        forwardOneList.add(forwardOne(c, destPortList[index], destDomain));
      });
      await Future.wait(forwardOneList);
      // await until hub sends 'exit' command (or an error occurrs)
      var error = await hubCommanderFinished.future;
      client.close();
      await client.done;
      return;
    } on io.SocketException catch (err) {
      print("B88675 can't connect to $sshDomain:$sshPort ($err)");
    } on ssh.SSHAuthAbortError catch (err) {
      print("B31284 can't connect to $sshDomain:$sshPort ($err)");
    } on ssh.SSHAuthFailError catch (err) {
      print("B61302 bad ssh key ($err)");
    } catch (err) {
      print("B50513 can't connect to $sshDomain: $err");
    }
  }

  Future<void> forwardOne(
      Stream forwardConnection, int destPort, destDomain) async {
    await for (final connection in forwardConnection) {
      var errStr = "";
      try {
        if (destPort == 0) {
          // hubCommander connection from hub
          final jsonStrings = JsonChunks(connection.stream);
          jsonStrings.stream.listen(
            (String json) {
              hubCommander(json, connection);
            },
            // fixme: make it work after disconnect → ¿broadcast stream (https://stackoverflow.com/a/70563131) or re-create new stream after close
            onError: (err) {
              print("B17234: $err");
              connection.sink.close();
            },
            onDone: () {
              if (!hubCommanderFinished.isCompleted) {
                print("B55482 connection closed unexpectedly");
                hubCommanderFinished.complete("connection closed unexpectedly");
              }
              connection.sink.close();
            },
          );
          return;
        }
        print("connection from server; trying $destDomain:$destPort");
        final socket = await io.Socket.connect(
          destDomain,
          destPort,
          timeout: const Duration(seconds: 20),
        );
        connection.stream.cast<List<int>>().pipe(socket);
        socket.pipe(connection.sink);
        print("connected to $destDomain:$destPort");
      } on io.SocketException catch (err) {
        errStr = "B22066 can't connect to $destDomain:$destPort ($err)";
      } catch (err) {
        errStr = "B58184 can't connect to $destDomain:$destPort ($err)";
      }
      if (errStr.isNotEmpty) {
        print(errStr);
        var bytes = Uint8List.fromList(("$errStr\n").codeUnits);
        connection.sink.add(bytes); // error message for server
        connection.sink.close();
      }
    }
  }

  void hubCommander(String json, connection) async {
    // process a command from the hub
    try {
      var command = convert.jsonDecode(json);
      command.forEach((key, value) {
        if (key == 'print') {
          print("hub: ${value['text']}");
        } else if (key == 'echo') {
          // echo text back to hub
          var bytes = Uint8List.fromList(("${value['text']}\n").codeUnits);
          connection.sink.add(bytes); // .write() doesn't exist
        } else if (key == 'exit') {
          // done with commands--close connection
          hubCommanderFinished.complete("");
        } else {
          print("B19842 unknown command: $key");
        }
      });
    } catch (err) {
      print("B50129 illegal command: ${json.trim()}");
    }
  }

  @override
  Widget build(BuildContext context) {
    const sizedBoxSpace = SizedBox(height: 24);
    return Form(
        key: formKey,
        autovalidateMode: AutovalidateMode.values[autoValidateModeIndex.value],
        child: Scrollbar(
            controller: scrollController,
            child: SingleChildScrollView(
              restorationId: 'new_server_screen_scroll_view',
              padding: const EdgeInsets.symmetric(horizontal: 34),
              controller: scrollController,
              child: Column(
                children: [
                  sizedBoxSpace,
                  const FractionallySizedBox(
                    widthFactor: 0.8,
                    child: Text(
                      "Set up a BitBurrow VPN server",
                      textAlign: TextAlign.center,
                      textScaleFactor: 1.8,
                      style: TextStyle(fontWeight: FontWeight.bold),
                    ),
                  ),
                  sizedBoxSpace,
                  FractionallySizedBox(
                    widthFactor: 0.6,
                    child: SvgPicture.asset("images/server-32983.svg"),
                  ),
                  sizedBoxSpace,
                  textMd(context, textBlob),
                  sizedBoxSpace,
                  sizedBoxSpace,
                  Center(
                    child: ElevatedButton(
                      onPressed: handleSubmitted,
                      child: const Text("I HAVE DONE THESE"),
                    ),
                  ),
                  sizedBoxSpace,
                ],
              ),
            )));
  }
}
