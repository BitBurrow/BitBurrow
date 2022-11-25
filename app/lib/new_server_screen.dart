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
        // print("B58185 can't connect to $toAddress:$toPort: $err");
        connection.sink.close();
      }
    }
    return "";
  }
}

class NewServerFormState extends ParentFormState {
  final _hubMessages = WebSocketMessenger();
  final _ssh = BbProxy();
  async.Completer _buttonPressed = async.Completer();
  final async.Completer _hubCommanderFinished = async.Completer();
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
          } else if (key == 'ssh_connect') {
            // ssh from app to hub
            _ssh.sshUser = value['ssh_user'];
            _ssh.sshKey = value['ssh_key'];
            _ssh.sshDomain = value['ssh_domain'];
            _ssh.sshPort = value['ssh_port'];
            result = await _ssh.connect();
            if (result == "") result = "okay";
          } else if (key == 'ssh_forward') {
            // port-forward port from hub to router over ssh
            result = await _ssh.forwardPort(
              fromPort: value['from_port'],
              toAddress: value['to_address'],
              toPort: value['to_port'],
            );
            if (result == "") result = "okay";
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
