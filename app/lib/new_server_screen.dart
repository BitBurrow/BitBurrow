import 'package:flutter/material.dart';
import 'package:flutter_svg/flutter_svg.dart';
import 'package:logging/logging.dart';
import 'logger_manager.dart' as logm;
import 'dart:io' as io;
import 'dart:async' as async;
import 'dart:convert' as convert;
import 'dart:typed_data';
import 'main.dart';
import 'parent_form_state.dart';
import 'step_box.dart';
import 'persistent_websocket.dart';

final _log = Logger('new_server_screen');
var loginState = LoginState.instance;

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
  final _hubMessages = PersistentWebSocket('', Logger('pws'));
  final hub = loginState.hub;
  final lk = loginState.pureLoginKey;
  async.Completer _buttonPressed = async.Completer();
  final async.Completer _hubCommanderFinished = async.Completer();
  final List<StepData> _stepsList = [];
  int _stepsComplete = 0;
  bool _needToScrollToBottom = false;

  NewServerFormState() : super() {
    var uri = Uri(
        scheme: 'wss',
        host: hub,
        port: 8443,
        path: '/v1/managers/$lk/servers/18/setup');
    try {
      _hubMessages.connect(uri).onError((err, stackTrace) {
        _log.warning("B47455 pws: $err");
      });
    } catch (err) {
      _log.warning("B13209 pws: $err");
    }
  }

  @override
  void initState() {
    super.initState();
    // add initial user steps, sent from hub
    WidgetsBinding.instance.addPostFrameCallback((_) async {
      _hubMessages.stream.listen(
        (data) => hubCommander(convert.utf8.decode(data)),
      );
    });
  }

  @override
  String get restorationId => 'new_server_form';

  @override
  String get lkocc => "null";

  @override
  Future<void> callApi() async {}

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
          } else if (key == 'proxy') {
            // WebSocket to hub to allow tcp connections to server
            result = await bbProxy(
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
            var manager = logm.LoggerManager();
            hubWrite({'log': manager.buffer.toString()});
            manager.buffer.clear();
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
    _hubMessages.send(Uint8List.fromList(convert.utf8.encode(json)));
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

  static Future<String> bbProxy({toAddress, toPort}) async {
    final hub = loginState.hub;
    final lk = loginState.pureLoginKey;
    final url = 'wss://$hub:8443/v1/managers/$lk/servers/18/proxy';
    io.WebSocket.connect(url).then((io.WebSocket ws) async {
      try {
        // new connection to target for each connection from hub
        final socket = await io.Socket.connect(
          toAddress,
          toPort,
          timeout: const Duration(seconds: 20),
        );
        // socket.handleError((err) {
        //   return "B48034 socket error {err}";
        // });
        ws.listen(
          (data) {
            socket.add(data);
          },
          onError: (err) {
            _log.warning("B47456 proxy WebSocket: $err");
          },
          onDone: () {
            _log.info('proxy connection to server closed');
          },
        );
        //connection.stream.cast<List<int>>().pipe(socket);
        ws.addStream(socket);
      } catch (err) {
        _log.warning("B58186 can't connect to $toAddress:$toPort: $err");
      }
    });
    return "";
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
                      physics: const NeverScrollableScrollPhysics(),
                      itemCount: _stepsList.length,
                      padding: const EdgeInsets.symmetric(horizontal: 18),
                      itemBuilder: stepBox,
                    ),
              sizedBoxSpace,
            ],
          ),
        ));
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
      // when all prior steps are complete, auto-scroll down to new one
      if (_stepsComplete > 0 && _stepsComplete >= _stepsList.length - 1) {
        _log.info("Request scroll to bottom (prior steps complete)");
        _needToScrollToBottom = true;
      }
    });
  }
}
