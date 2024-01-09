import 'package:flutter/material.dart';
import 'package:flutter_svg/flutter_svg.dart';
import 'package:logging/logging.dart';
import 'dart:io' as io;
import 'dart:async' as async;
import 'main.dart';
import 'parent_form_state.dart';
import 'step_box.dart';
import 'hub_rpc.dart';

final _log = Logger('new_base_screen');
var loginState = LoginState.instance;

class NewBaseScreen extends StatelessWidget {
  const NewBaseScreen({Key? key}) : super(key: key);

  @override
  Widget build(BuildContext context) => ourScreenLayout(
        context,
        const NewBaseForm(),
      );
}

class NewBaseForm extends ParentForm {
  const NewBaseForm({Key? key}) : super(key: key);

  @override
  NewBaseFormState createState() => NewBaseFormState();
}

class NewBaseFormState extends ParentFormState {
  final rpc = HubRpc.instance;
  async.Completer _buttonPressed = async.Completer();
  final List<StepData> _stepsList = [];
  int _stepsComplete = 0; // local steps, always sequential
  bool _needToScrollToBottom = false;
  int _nextTask = 0; // task number from hub
  int _baseId = -1; // database record id

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) async {
      // begin processing tasks after screen is drawn
      taskTurner();
    });
  }

  @override
  String get restorationId => 'new_base_form';

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

  /// Tell hub to do a task; process a task from hub; repeat.
  Future taskTurner() async {
    while (true) {
      var response = await rpc.sendRequest(
        'create_base',
        {
          'login_key': loginState.pureLoginKey,
          'task_id': _nextTask,
          'base_id': _baseId,
        },
      );
      String method;
      Map params;
      int newBaseId;
      try {
        method = response['method'];
        params = response['params'];
        _nextTask = response['next_task'];
        newBaseId = response['base_id'];
      } catch (err) {
        throw "B91194 invalid response $response";
      }
      if (_baseId != -1 && _baseId != newBaseId) {
        throw "B81752 base_id changed ($_baseId != $newBaseId}";
      }
      _baseId = newBaseId;
      if (method == 'finished') {
        break;
      }
      await hubCommander(method, params);
    }
  }

  /// Process one command from the hub.
  Future hubCommander(String method, Map params) async {
    if (method == 'print') {
      // print text to logging system
      _log.info("hub: ${params['text']}");
    } else if (method == 'add_checkbox_step') {
      // add a checkbox step to the list of steps displayed for the user
      addStep(StepData(text: params['text'], type: StepTypes.checkbox));
    } else if (method == 'add_process_step') {
      // ... or a process step
      addStep(StepData(text: params['text'], type: StepTypes.process));
    } else if (method == 'add_button_step') {
      // ... or a button
      _buttonPressed = async.Completer(); // reset to unpressed state
      addStep(StepData(text: params['text'], type: StepTypes.button));
      await _buttonPressed.future;
      // } else if (method == 'get_user_input') {
      //   // prompt user, return response; all args optional
      //   String result = await promptDialog(
      //         context: context,
      //         title: params['title'],
      //         text: params['text'],
      //         labelText: params['label_text'],
      //         buttonText: params['button_text'],
      //         cancelButtonText: params['cancel_button_text'],
      //       ) ??
      //       "cancel_button_53526168";
    } else if (method == 'sleep') {
      // delay processing of subsequent commands
      int seconds = params['seconds'] ?? 0;
      int ms = params['ms'] ?? 0 + seconds * 1000;
      await Future.delayed(Duration(milliseconds: ms), () {});
      // } else if (method == 'proxy') {
      //   // WebSocket to hub to allow tcp connections to base
      //   result = await bbProxy(
      //     toAddress: params['to_address'],
      //     toPort: params['to_port'],
      //   );
      //   if (result == "") result = "okay";
      // } else if (method == 'get_if_list') {
      //   // return list of network interfaces and IP addresses
      //   hubWrite(await ifList());
      //   return;
      // } else if (method == 'dump_and_clear_log') {
      //   // return log entries, empty the buffer
      //   var manager = logm.LoggerManager();
      //   hubWrite({'log': manager.buffer.toString()});
      //   manager.buffer.clear();
      //   return;
    } else {
      throw "B19842 unknown command: $method";
    }
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
    final url = 'wss://$hub:8443/v1/managers/$lk/bases/18/proxy';
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
          restorationId: 'new_base_screen_scroll_view',
          padding: const EdgeInsets.symmetric(horizontal: 34),
          controller: scrollController,
          child: Column(
            children: [
              sizedBoxSpace,
              const FractionallySizedBox(
                widthFactor: 0.8,
                child: Text(
                  "Set up a BitBurrow VPN base",
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
