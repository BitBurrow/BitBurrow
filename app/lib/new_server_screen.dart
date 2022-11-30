// ignore_for_file: avoid_print

import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'package:flutter_svg/flutter_svg.dart';
import 'dart:io' as io;
import 'dart:async' as async;
import 'dart:convert' as convert;
import 'main.dart';
import 'parent_form_state.dart';
import 'bb_proxy.dart';

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

  Widget stepBox(contents, index) => StepBox(
        onCheckboxTap: (newValue) {
          if (index < (_stepsProgress - 1)) {
            if (_stepsType[_stepsType.length - 1] != StepTypes.process) {
              // skip snackbar when a process is pending
              showInSnackBar("Uncheck items at the bottom of the "
                  "list first.");
            }
            return;
          } else if (index > _stepsProgress) {
            showInSnackBar("You must check items in order from "
                "top to bottom.");
            return;
          } else {
            setState(() {
              _stepsProgress = index + (newValue == true ? 1 : 0);
            });
          }
        },
        onButtonPress: () {
          setState(() {
            _stepsProgress += 1;
            _buttonPressed.complete("pressed");
          });
        },
        text: _stepsText[index],
        type: _stepsType[index],
        isChecked: index < _stepsProgress,
        isActive: index == _stepsProgress || index == _stepsProgress - 1,
        isLastStep: index == _stepsText.length - 1,
      );

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

class StepBox extends StatelessWidget {
  const StepBox({
    super.key,
    this.onCheckboxTap,
    this.onButtonPress,
    required this.text,
    required this.type,
    required this.isChecked, // or 'pressed' for buttons
    required this.isActive, // last checked step or first unchecked step
    required this.isLastStep,
  });

  final void Function(bool?)? onCheckboxTap;
  final void Function()? onButtonPress;
  final String text;
  final StepTypes type;
  final bool isChecked;
  final bool isActive;
  final bool isLastStep;

  @override
  Widget build(context) {
    bool isCheckbox = type == StepTypes.checkbox;
    bool isProcess = type == StepTypes.process;
    bool isButton = type == StepTypes.button;
    bool isNextStep = isActive && !isChecked;
    return isButton
        // StepTypes.button
        ? Column(
            children: !isLastStep
                ? [] // hide button when it's not the last step
                : [
                    const SizedBox(height: 24),
                    Center(
                      child: ElevatedButton(
                        onPressed: isNextStep
                            ? onButtonPress
                            : null, // disabled until all steps are done
                        child: Text(text.trim()),
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
                              value: isChecked,
                              onChanged: onCheckboxTap,
                            )
                          : (isProcess && !isNextStep)
                              ? Checkbox(
                                  value: isChecked ? true : null,
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
                  textMd(context, text),
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
}
