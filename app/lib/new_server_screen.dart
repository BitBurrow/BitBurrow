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
  List<Future> forwardsList = [];
  var guiMessages = async.StreamController<String>();

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
    guiMessages = async.StreamController<String>();
    var progressDialog = showPopupDialog(
      context: context,
      title: "Configuring BitBurrow VPN server ...",
      messages: guiMessages.stream,
      buttonText: "CANCEL",
    );
    var dialogState = DialogStates.open;
    progressDialog.whenComplete(() {
      if (dialogState == DialogStates.open) {
        print("B29626 user canceled dialog before request completed");
        dialogState = DialogStates.canceled;
        // FIXME: gracefully cancel router config and ssh connection
      } else {
        dialogState = DialogStates.closed;
      }
    });
    var error = "";
    try {
      if (sshLogin != null) {
        await sshConnect(
          sshUser: sshLogin!['ssh_user'],
          sshKey: sshLogin!['ssh_key'],
          sshDomain: sshLogin!['ssh_domain'],
          sshPort: sshLogin!['ssh_port'],
          forwardFromPort: sshLogin!['forward_from_port'],
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

  Future sshConnect({
    sshUser,
    sshKey, // actual key contents
    sshDomain,
    sshPort = 22,
    forwardFromPort,
  }) async {
    var tries = 0;
    Stopwatch stopwatch = Stopwatch()..start();
    var error = "never assigned";
    while (true) {
      try {
        hubCommanderFinished = async.Completer();
        final socket = await ssh.SSHSocket.connect(sshDomain, sshPort);
        final client = ssh.SSHClient(
          socket,
          username: sshUser,
          identities: ssh.SSHKeyPair.fromPem(sshKey),
        );
        await client.authenticated;
        forwardsList.add(forwardCommandChannel(
          client: client,
          fromPort: forwardFromPort,
        ));
        // await until hub sends 'exit' command (or an error occurrs)
        error = await hubCommanderFinished.future;
        client.close();
        await client.done;
      } on io.SocketException catch (err) {
        print("B88675 can't connect to $sshDomain:$sshPort: $err");
      } on ssh.SSHAuthAbortError catch (err) {
        print("B31284 can't connect to $sshDomain:$sshPort: $err");
      } on ssh.SSHAuthFailError catch (err) {
        print("B61302 bad ssh key: $err");
      } on ssh.SSHStateError catch (err) {
        print("B88975 ssh connection failed: $err"); // e.g. server proc killed
      } catch (err) {
        print("B50513 can't connect to $sshDomain: $err");
      }
      if (error.isEmpty) break; // success
      tries += 1;
      if (tries >= 7) {
        if (stopwatch.elapsedMilliseconds < 2000) {
          print("B34362 7 tries in 2 seconds--giving up");
          break; // 7 tries in 2 seconds--don't keep trying
        } else {
          tries = 0;
          stopwatch = Stopwatch()..start();
        }
      }
      print("B08226 retrying ssh connection; last error was: $error");
    }
  }

  Future<void> forwardCommandChannel({
    required ssh.SSHClient client,
    required int fromPort,
  }) async {
    final forward = await client.forwardRemote(port: fromPort);
    if (forward == null) {
      print("B35541 can't forward from_port $fromPort");
      return;
    }
    await for (final connection in forward.connections) {
      try {
        // hubCommander connection from hub
        final jsonStrings = JsonChunks(connection.stream);
        // breaks ordering, esp. 'sleep': jsonStrings.stream.listen(...)
        await for (final json in jsonStrings.stream) {
          try {
            await hubCommander(json, client, connection);
          } catch (err) {
            print("B17234: $err");
            connection.sink.close();
          }
        }
        if (!hubCommanderFinished.isCompleted) {
          print("B55482 connection closed unexpectedly");
          hubCommanderFinished.complete("connection closed unexpectedly");
        }
        connection.sink.close();
      } catch (err) {
        print("B58184 command channel failed: $err");
      }
    }
  }

  Future<void> forwardToRouter({
    required ssh.SSHClient client,
    required int fromPort,
    required int toPort,
    String toAddress = '',
  }) async {
    final forward = await client.forwardRemote(port: fromPort);
    if (forward == null) {
      print("B35542 can't forward fromPort $fromPort");
      return;
    }
    await for (final connection in forward.connections) {
      try {
        print("connection from server; trying $toAddress:$toPort");
        final socket = await io.Socket.connect(
          toAddress,
          toPort,
          timeout: const Duration(seconds: 20),
        );
        connection.stream.cast<List<int>>().pipe(socket);
        socket.pipe(connection.sink);
        print("connected to $toAddress:$toPort");
      } catch (err) {
        print("B58185 can't connect to $toAddress:$toPort: $err");
        connection.sink.close();
      }
    }
    await Future.wait(forwardsList);
  }

  Future<void> hubCommander(String json, client, connection) async {
    // process one command from the hub
    try {
      var command = convert.jsonDecode(json);
      // breaks ordering, esp. 'sleep': command.forEach((key, value) async {...}
      for (final e in command.entries) {
        var key = e.key;
        var value = e.value;
        try {
          if (key == 'print') {
            // print text in app console
            print("hub: ${value['text']}");
          } else if (key == 'show_md') {
            // display Markdown in the user dialog
            guiMessages.sink.add(value['markdown']);
          } else if (key == 'echo') {
            // echo text back to hub
            hubWrite(
                "${value['text']}\n", connection); // .write() doesn't exist
          } else if (key == 'sleep') {
            // delay processing of subsequent commands
            await Future.delayed(Duration(seconds: value['seconds']), () {});
          } else if (key == 'ssh_forward') {
            forwardToRouter(
              client: client,
              fromPort: value['from_port'],
              toAddress: value['to_address'],
              toPort: value['to_port'],
            );
          } else if (key == 'exit') {
            // done with commands--close TCP connection
            hubCommanderFinished.complete("");
          } else {
            print("B19842 unknown command: $key");
          }
        } catch (err) {
          print("B18332 illegal arguments: ${json.trim()}");
        }
      }
    } catch (err) {
      print("B50129 illegal command ${json.trim()}: "
          "${err.toString().replaceAll(RegExp(r'[\r\n]+'), ' ¶ ')}");
    }
  }

  void hubWrite(String text, connection) {
    var bytes = convert.utf8.encode(text);
    connection.sink.add(bytes); // .write() doesn't exist
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
