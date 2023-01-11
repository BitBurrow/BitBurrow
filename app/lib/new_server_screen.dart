import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'package:flutter_svg/flutter_svg.dart';
import 'package:logging/logging.dart';
import 'dart:io' as io;
import 'dart:async' as async;
import 'dart:convert' as convert;
import 'global.dart' as global;
import 'main.dart';
import 'parent_form_state.dart';
import 'bb_proxy.dart';
import 'step_box.dart';

final _log = Logger('new_server_screen');

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

class WebSocketMessenger {
  io.WebSocket? _ws;
  final async.Completer _connected = async.Completer();
  final _inbound = async.StreamController<String>();

  WebSocketMessenger() {
    final wsPath =
        '/v1/accounts/${global.loginState.pureLoginKey}/servers/18/setup_ws';
    final url = 'wss://${global.loginState.hub}:8443$wsPath';
    // fixme: WebSocket.connect() may raise "WebSocketException: Connection
    //   to ... was not upgraded to websocket" but try-catch misses it
    io.WebSocket.connect(url).then((io.WebSocket socket) async {
      _ws = socket;
      if (_ws == null) {
        _log.warning("B10647 WebSocket can't connect");
        return;
      }
      _connected.complete('connected');
      _ws!.listen(
        (message) {
          _inbound.sink.add(message);
        },
        onError: (err) {
          _log.warning("B47455 WebSocket: $err");
        },
        onDone: () {
          _log.info('connection to server closed');
        },
      );
    });
  }

  Future<void> add(message) async {
    await _connected.future; // wait until connected
    if (_ws == null) {
      _log.warning("B10648 WebSocket can't connect");
    } else {
      _ws!.add(message);
    }
  }

  Future<void> close(message) async {
    await _connected.future; // wait until connected
    if (_ws == null) {
      _log.warning("B10649 WebSocket can't connect");
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
  final List<StepData> _stepsList = [];
  int _stepsComplete = 0;
  Stream<String>? _activeStepMessages;
  bool _needToScrollToBottom = false;

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
  String statusCodeCheck(status) => "Not implemented.";

  @override
  String processApiResponse(response) => "Not implemented.";

  @override
  nextScreen() {
    return;
  }

  @override
  String getHubValue() => global.loginState.hub;

  @override
  void setHubValue(value) {
    global.loginState.hub = value;
  }

  @override
  String getAccountValue() => "";

  @override
  void setAccountValue(value) {
    global.loginState.loginKey = value;
  }

  Future<void> hubCommander(String json) async {
    // process one command from the hub
    _log.info("hubCommand ${json.trim().characters.take(90)}"); // max 2 lines
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
            // print text to logging system
            _log.info("hub: ${value['text']}");
          } else if (key == 'add_checkbox_step') {
            // add a checkbox step to the list of steps displayed for the user
            addStep(StepData(text: value['text'], type: StepTypes.checkbox));
          } else if (key == 'add_process_step') {
            // ... or a process step
            addStep(StepData(text: value['text'], type: StepTypes.process));
          } else if (key == 'add_button_step') {
            // ... or a button
            _buttonPressed = async.Completer(); // reset to unpressed state
            addStep(StepData(text: value['text'], type: StepTypes.button));
            await _buttonPressed.future;
          } else if (key == 'get_user_input') {
            // prompt user, return response; all args optional
            result = await promptDialog(
                  context: context,
                  title: value['title'],
                  text: value['text'],
                  labelText: value['label_text'],
                  buttonText: value['button_text'],
                  cancelButtonText: value['cancel_button_text'],
                ) ??
                "cancel_button_53526168";
          } else if (key == 'echo') {
            // echo text back to hub
            result = value['text'];
          } else if (key == 'sleep') {
            // delay processing of subsequent commands
            int seconds = value['seconds'] ?? 0;
            int ms = value['ms'] ?? 0 + seconds * 1000;
            await Future.delayed(Duration(milliseconds: ms), () {});
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
          } else if (key == 'dump_and_clear_log') {
            // return log entries, empty the buffer
            hubWrite({'log': global.logMan.buffer.toString()});
            global.logMan.buffer.clear();
            return;
          } else if (key == 'exit') {
            // done with commands--close TCP connection
            _hubCommanderFinished.complete("");
          } else {
            result = "B19842 unknown command: $key";
          }
        } catch (err) {
          result = "B18332 illegal arguments: $err";
        }
      }
    } catch (err) {
      result = "B50129 illegal command: "
          "${err.toString().replaceAll(RegExp(r'[\r\n]+'), ' ¶ ')}";
    }
    _log.info("hubCommand result: $result");
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
      for (var address in interface.addresses) {
        i.add(address.address.toString());
      }
      result[interface.name] = i;
    }
    return result;
  }

  @override
  Widget build(BuildContext context) {
    if (_needToScrollToBottom) {
      // scroll only after Flutter rebuilt and render; see
      // https://smarx.com/posts/2020/08/automatic-scroll-to-bottom-in-flutter/
      WidgetsBinding.instance.addPostFrameCallback((_) {
        _log.fine("Scroll to bottom");
        scrollController.animateTo(scrollController.position.maxScrollExtent,
            duration: const Duration(milliseconds: 1000), curve: Curves.ease);
      });
      _needToScrollToBottom = false;
    }
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
                  _stepsList.isEmpty
                      ? textMd(context, "(waiting for hub)")
                      : ListView.builder(
                          shrinkWrap: true,
                          itemCount: _stepsList.length,
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
          _log.fine("CheckBox $index tap; $_stepsComplete steps were complete; "
              "newValue ${newValue == true ? '✔' : newValue == false ? '☐' : '━'}");
          if (index < (_stepsComplete - 1)) {
            if (_stepsList[_stepsList.length - 1].type != StepTypes.process) {
              // skip snackbar when a process is pending
              showInSnackBar("Uncheck items at the bottom of the "
                  "list first.");
            }
            return;
          } else if (index > _stepsComplete) {
            showInSnackBar("You must check items in order from "
                "top to bottom.");
            return;
          } else {
            setState(() {
              _stepsComplete = index + (newValue == true ? 1 : 0);
              // if it's the last checkbox, auto-scroll down
              if (_stepsComplete >= _stepsList.length - 1) {
                _log.info("Request scroll to bottom (last checkbox)");
                _needToScrollToBottom = true;
              }
            });
          }
        },
        onButtonPress: () {
          _log.fine("ElevatedButton '${_stepsList[index].text.trim()}' "
              "onPressed()");
          setState(() {
            _stepsComplete += 1;
            _buttonPressed.complete("pressed");
          });
        },
        data: _stepsList[index],
        isChecked: index < _stepsComplete,
        isActive: index == _stepsComplete || index == _stepsComplete - 1,
        isLastStep: index == _stepsList.length - 1,
      );

  void addStep(
    StepData data, {
    Stream<String>? messages,
  }) {
    setState(() {
      // if prior step was a process, assume it is now complete (checkboxes
      // ... and buttons rely on user input)
      if (_stepsList.isNotEmpty &&
          _stepsList[_stepsList.length - 1].type == StepTypes.process) {
        _stepsComplete += 1; // only the last step can be a pending process
      }
      _stepsList.add(data);
      _activeStepMessages = messages;
      // when all prior steps are complete, auto-scroll down to new one
      if (_stepsComplete > 0 && _stepsComplete >= _stepsList.length - 1) {
        _log.info("Request scroll to bottom (prior steps complete)");
        _needToScrollToBottom = true;
      }
    });
  }
}
