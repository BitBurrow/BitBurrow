// ignore_for_file: avoid_print

import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'package:flutter_svg/flutter_svg.dart';
import 'package:dartssh2/dartssh2.dart' as ssh;
import 'dart:io' as io;
import 'dart:async' as async;
import 'dart:convert' as convert;
import 'main.dart';

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

enum StepTypes {
  checkbox, // user can check, uncheck
  process, // automated, has cancel button, can be retried
  button, // e.g. "CONFIGURE ROUTER"
}

class WebSocketMessenger {
  io.WebSocket? _ws;
  final async.Completer _connected = async.Completer();
  final _inbound = async.StreamController<String>();

  WebSocketMessenger() {
    final wsPath = '/v1/accounts/${loginState.loginKey}/servers_ws';
    final url = 'ws://${loginState.hub}:8443$wsPath';
    io.WebSocket.connect(url).then((io.WebSocket socket) async {
      _ws = socket;
      if (_ws == null) {
        print("B10647 WebSocket can't connect");
        return;
      }
      _connected.complete('connected');
      _ws!.listen(
        (message) {
          _inbound.sink.add(message);
        },
        onError: (err) {
          print("B4745 WebSocket: $err");
        },
        onDone: () {
          print('connection to server closed');
        },
      );
    });
  }

  Future<void> add(message) async {
    await _connected.future; // wait until connected
    if (_ws == null) {
      print("B10648 WebSocket can't connect");
    } else {
      _ws!.add(message);
    }
  }

  Future<void> close(message) async {
    await _connected.future; // wait until connected
    if (_ws == null) {
      print("B10649 WebSocket can't connect");
    } else {
      _ws!.close;
    }
  }

  Stream<String> get stream => _inbound.stream;
}

class NewServerFormState extends ParentFormState {
  Map<String, dynamic>? _sshLogin;
  final _hubMessages = WebSocketMessenger();
  async.Completer _buttonPressed = async.Completer();
  final async.Completer _hubCommanderFinished = async.Completer();
  final List<Future> _forwardsList = [];
  final List<String> _stepsText = [];
  final List<StepTypes> _stepsType = [];
  int _stepsProgress = 0;
  Stream<String>? _activeStepMessages;

  @override
  void initState() {
    super.initState();
    // add initial user steps, sent from hub
    WidgetsBinding.instance.addPostFrameCallback((_) async {
      _hubMessages.stream.listen(
        (message) => hubCommander(message),
      );
    });
  }

  @override
  String get restorationId => 'new_server_form';

  @override
  Future<http.Response?> callApi() => Future<http.Response?>.value(null);

  @override
  String validateStatusCode(status) => "Not implemented.";

  @override
  String processApiResponse(response) => "Not implemented.";

  @override
  nextScreen() {
    return;
  }

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

  // Future bbProxy() async {
  //   var error = "";
  //   try {
  //     if (_sshLogin != null) {
  //       sshConnect(
  //         sshUser: _sshLogin!['ssh_user'],
  //         sshKey: _sshLogin!['ssh_key'],
  //         sshDomain: _sshLogin!['ssh_domain'],
  //         sshPort: _sshLogin!['ssh_port'],
  //         forwardFromPort: _sshLogin!['forward_from_port'],
  //       );
  //     } else {
  //       error = "B12944 sshLogin is null";
  //     }
  //   } catch (err) {
  //     error = err.toString();
  //   }
  //   var displayError = "";
  // }

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
        final socket = await ssh.SSHSocket.connect(sshDomain, sshPort);
        final client = ssh.SSHClient(
          socket,
          username: sshUser,
          identities: ssh.SSHKeyPair.fromPem(sshKey),
        );
        await client.authenticated;
        // _forwardsList.add(forwardCommandChannel(
        //   client: client,
        //   fromPort: forwardFromPort,
        // ));
        // await until hub sends 'exit' command (or an error occurrs)
        error = await _hubCommanderFinished.future;
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
    await Future.wait(_forwardsList);
  }

  Future<void> hubCommander(String json) async {
    // process one command from the hub
    var result = "okay";
    try {
      var command = convert.jsonDecode(json);
      // breaks ordering, esp. 'sleep': command.forEach((key, value) async {...}
      var itemCount = 0;
      for (final e in command.entries) {
        itemCount += 1;
        assert(itemCount == 1);
        var key = e.key;
        var value = e.value;
        try {
          if (key == 'print') {
            // print text in app console
            print("hub: ${value['text']}");
          } else if (key == 'add_checkbox_step') {
            // add a checkbox step to the list of steps displayed for the user
            addStep(text: value['text'], type: StepTypes.checkbox);
          } else if (key == 'add_process_step') {
            // ... or a process step
            addStep(text: value['text'], type: StepTypes.process);
          } else if (key == 'add_button_step') {
            // ... or a button
            _buttonPressed = async.Completer(); // reset to unpressed state
            addStep(text: value['text'], type: StepTypes.button);
            await _buttonPressed.future;
          } else if (key == 'echo') {
            // echo text back to hub
            result = value['text'];
          } else if (key == 'sleep') {
            // delay processing of subsequent commands
            await Future.delayed(Duration(seconds: value['seconds']), () {});
            // } else if (key == 'ssh_connect') {
            //   // ssh from app to hub
            //   FIXME: next line seems to return immediately AND not fail when it can't connect
            //   await sshConnect(
            //     sshUser: value['ssh_user'],
            //     sshKey: value['ssh_key'],
            //     sshDomain: value['ssh_domain'],
            //     sshPort: value['ssh_port'],
            //     forwardFromPort: value['forward_from_port'],
            //   );
            // } else if (key == 'ssh_forward') {
            //   // port-forward port from hub to router over ssh
            //   forwardToRouter(
            //     client: client,
            //     fromPort: value['from_port'],
            //     toAddress: value['to_address'],
            //     toPort: value['to_port'],
            //   );
          } else if (key == 'get_if_list') {
            // return list of network interfaces and IP addresses
            hubWrite(await ifList());
            return;
          } else if (key == 'exit') {
            // done with commands--close TCP connection
            _hubCommanderFinished.complete("");
          } else {
            result = "B19842 unknown command: $key";
          }
        } catch (err) {
          result = "B18332 illegal arguments ${json.trim()}: $err";
        }
      }
    } catch (err) {
      result = "B50129 illegal command ${json.trim()}: "
          "${err.toString().replaceAll(RegExp(r'[\r\n]+'), ' ¶ ')}";
    }
    if (result != "okay") print(result);
    hubWrite({'result': result});
  }

  void hubWrite(Map<String, dynamic> data) {
    var json = convert.jsonEncode(data);
    _hubMessages.add(json);
  }

  static Future<Map<String, dynamic>> ifList() async {
    final interfaces = await io.NetworkInterface.list(includeLinkLocal: true);
    Map<String, dynamic> result = {};
    for (var interface in interfaces) {
      List<String> i = [];
      print("${interface.name} →");
      for (var address in interface.addresses) {
        print("    ${address.address}");
        i.add(address.address.toString());
      }
      result[interface.name] = i;
    }
    return result;
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
                  textMd(
                      context,
                      "These steps should be done at "
                      "your \"VPN home\" location. Check the box on the left "
                      "as you complete each step."),
                  sizedBoxSpace,
                  // TODO: use AnimatedList() // https://www.youtube.com/watch?v=ZtfItHwFlZ8
                  ListView.builder(
                    shrinkWrap: true,
                    itemCount: _stepsText.length,
                    padding: const EdgeInsets.symmetric(horizontal: 18),
                    itemBuilder: stepBox,
                  ),
                  sizedBoxSpace,
                ],
              ),
            )));
  }

  Widget stepBox(context, index) {
    bool isCheckbox = _stepsType[index] == StepTypes.checkbox;
    bool isProcess = _stepsType[index] == StepTypes.process;
    bool isNextStep = index == _stepsProgress;
    return (_stepsType[index] == StepTypes.button)
        // StepTypes.button
        ? Column(
            children: _stepsText.length - 1 != index
                ? [] // hide button when it's not the last step
                : [
                    const SizedBox(height: 24),
                    Center(
                      child: ElevatedButton(
                        onPressed: isNextStep
                            ? () {
                                setState(() {
                                  _stepsProgress += 1;
                                  _buttonPressed.complete("pressed");
                                });
                              }
                            : null, // disabled until all steps are done
                        child: Text(_stepsText[index].trim()),
                      ),
                    ),
                  ],
          )
        // StepTypes.checkbox OR StepTypes.process
        : Row(
            crossAxisAlignment: CrossAxisAlignment.start, // top-align
            children: [
              // checkbox
              SizedBox(
                width: 52, // 3 + 26 + 23
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start, // top-align
                  children: [
                    // SizedBox() sizes below mimic CheckboxListTile() with:
                    //   controlAffinity: ListTileControlAffinity.leading,
                    //   contentPadding: EdgeInsets.zero, dense: true,
                    const SizedBox(height: 52, width: 3),
                    SizedBox(
                      height: 26,
                      width: 26,
                      child: isCheckbox
                          ? Checkbox(
                              value: _stepsProgress > index,
                              onChanged: (newValue) {
                                if (index < (_stepsProgress - 1)) {
                                  if (_stepsType[_stepsType.length - 1] !=
                                      StepTypes.process) {
                                    // skip snackbar when a process is pending
                                    showInSnackBar(
                                        "Uncheck items at the bottom of the "
                                        "list first.");
                                  }
                                  return;
                                } else if (index > _stepsProgress) {
                                  showInSnackBar(
                                      "You must check items in order from "
                                      "top to bottom.");
                                  return;
                                } else {
                                  setState(() {
                                    _stepsProgress =
                                        index + (newValue == true ? 1 : 0);
                                  });
                                }
                              },
                            )
                          : (isProcess && !isNextStep)
                              ? Checkbox(
                                  value: _stepsProgress > index ? true : null,
                                  tristate: true,
                                  onChanged: null,
                                )
                              : Transform.scale(
                                  scale: 1.4,
                                  child: const CircularProgressIndicator(
                                    strokeWidth: 4,
                                  ),
                                ),
                    ),
                  ],
                ),
              ),
              // title and text
              Expanded(
                  child: Column(
                crossAxisAlignment: CrossAxisAlignment.start, // left-align text
                children: [
                  textMd(context, _stepsText[index]),
                  if (isNextStep && isProcess)
                    Row(
                      mainAxisAlignment:
                          MainAxisAlignment.end, // right-align button
                      children: [
                        TextButton(
                            onPressed: () {}, child: const Text("CANCEL"))
                      ],
                    ),
                  const SizedBox(height: 16), // spacing between steps
                ],
              )),
            ],
          );
  }

  void addStep({
    required String text,
    required StepTypes type,
    Stream<String>? messages,
  }) {
    setState(() {
      // if prior step was a process, assume it is now complete (checkboxes
      // ... and buttons rely on user input)
      if (_stepsType.isNotEmpty &&
          _stepsType[_stepsType.length - 1] == StepTypes.process) {
        _stepsProgress += 1; // only the last step can be a pending process
      }
      _stepsText.add(text);
      _stepsType.add(type);
      _activeStepMessages = messages;
    });
  }
}
