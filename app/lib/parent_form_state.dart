import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:http/http.dart' as http;
import 'package:flutter_svg/flutter_svg.dart';
import 'package:logging/logging.dart';
import 'global.dart' as global;
import 'main.dart';

final _log = Logger('parent_form_state');

const String base28Digits = '23456789BCDFGHJKLMNPQRSTVWXZ';
const int accountLen = 21; // including dashes
const String aBase28Digit = '[$base28Digits]';
final accountRE = RegExp(
    '($aBase28Digit{4})-$aBase28Digit{5}-$aBase28Digit{4}-$aBase28Digit{5}');
String accountREReplace(Match m) => "${m[1]}-.....-....-.....";
final pureAccountRE = RegExp(
    '($aBase28Digit{4})$aBase28Digit{5}$aBase28Digit{4}$aBase28Digit{5}');
String pureAccountREReplace(Match m) => "${m[1]}..............";

//                                               123456789012345678901
// Strip illegal chars, format incoming text as: XXXX-XXXXX-XXXX-XXXXX
class _AccountTextInputFormatter extends TextInputFormatter {
  @override
  TextEditingValue formatEditUpdate(
    TextEditingValue oldValue,
    TextEditingValue newValue,
  ) {
    var before = newValue.text.toUpperCase();
    var beforePos = newValue.selection.end;
    var beforeLength = before.length;
    final after = StringBuffer();
    var afterPos = 0;
    String unbackspaceTest =
        '${before.substring(0, beforePos)}-${before.substring(beforePos)}';
    if (unbackspaceTest == oldValue.text) {
      // if user backspaces over '-', delete the character before
      before =
          '${before.substring(0, beforePos - 1)}${before.substring(beforePos)}';
      beforePos = beforePos - 1;
      beforeLength = before.length;
    }
    for (int i = 0; i < beforeLength; i++) {
      if (i == beforePos) afterPos = after.length;
      var c = before[i];
      if (base28Digits.contains(c)) after.write(c);
      var l = after.length;
      if (l == 4 || l == 10 || l == 15) after.write('-');
    }
    if (beforeLength == beforePos) afterPos = after.length;
    return TextEditingValue(
      text: after.toString(),
      selection: TextSelection.collapsed(offset: afterPos),
    );
  }
}

extension StringExtension on String {
  String capitalize() {
    return "${this[0].toUpperCase()}${substring(1).toLowerCase()}";
  }
}

enum DialogStates {
  open,
  closing,
  closed,
  canceled,
}

abstract class ParentForm extends StatefulWidget {
  const ParentForm({Key? key}) : super(key: key);
}

abstract class ParentFormState extends State<ParentForm> with RestorationMixin {
  // based on https://github.com/flutter/gallery/blob/d030f1e5316310c48fc725f619eb980a0597366d/lib/demos/material/text_field_demo.dart
  bool _isObscure = true;
  final scrollController = ScrollController();

  void showInSnackBar(String value) {
    _log.fine("showInSnackBar() $value");
    ScaffoldMessenger.of(context).hideCurrentSnackBar();
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(
      content: Text(value),
    ));
  }

  @override
  void restoreState(RestorationBucket? oldBucket, bool initialRestore) {
    registerForRestoration(autoValidateModeIndex, 'autovalidate_mode');
  }

  final RestorableInt autoValidateModeIndex =
      RestorableInt(AutovalidateMode.disabled.index);

  final GlobalKey<FormState> formKey = GlobalKey<FormState>();
  final GlobalKey<FormState> dialogFormKey = GlobalKey<FormState>();
  final _AccountTextInputFormatter _accountFormatter =
      _AccountTextInputFormatter();

  Future<http.Response?> callApi();
  String validateStatusCode(status);
  String processApiResponse(response);
  nextScreen();
  String getHubValue();
  void setHubValue(String value);
  String getAccountValue();
  void setAccountValue(String value);

  bool validateTextFields() {
    final form = formKey.currentState!;
    if (!form.validate()) {
      autoValidateModeIndex.value =
          AutovalidateMode.always.index; // Start validating on every change.
      return true;
    }
    return false;
  }

  void handleSubmitted() async {
    final form = formKey.currentState!;
    form.save();
    var hub = getHubValue(); // if user cancels dialog, hub and ...
    // getHubValue() may not be the same because this method is re-entered
    var connectingDialog = notificationDialog(
      context: context,
      title: "Connecting to hub ...",
      buttonText: "CANCEL",
    );
    var dialogState = DialogStates.open;
    connectingDialog.whenComplete(() {
      if (dialogState == DialogStates.open) {
        _log.info("user canceled dialog before request completed");
        dialogState = DialogStates.canceled;
      } else {
        dialogState = DialogStates.closed;
      }
    });
    var futureDelay = Future.delayed(const Duration(seconds: 1), () {});
    http.Response? response;
    var error = "";
    try {
      response = await callApi()
          .timeout(const Duration(seconds: 45)); // default 120 in my test
    } catch (err) {
      error = err.toString();
    }
    await futureDelay; // ensure user always sees that something is happening
    if (dialogState == DialogStates.canceled) {
      // ignore user-canceled result, successful or not
      _log.info(
          "(finished http $hub but ignoring because it was user-canceled)");
      return;
    }
    if (!mounted) {
      _log.warning("B25600 finished http $hub but !mounted");
      return;
    }
    dialogState = DialogStates.closing;
    Navigator.pop(context); // close dialog
    var displayError = "";
    if (error.isEmpty) {
      if (response == null) {
        error = "B29348 response is null";
      } else {
        displayError = validateStatusCode(response.statusCode);
        if (displayError.isNotEmpty) {
          error = "B14514 invalid status code ${response.statusCode}";
        } else {
          // status code is okay
          try {
            error = processApiResponse(response);
            if (error.isNotEmpty) {
              displayError = "Received invalid data from the hub. Contact the "
                  "hub administrator.";
            } else {
              _log.fine("successful connection to $hub, "
                  "status code ${response.statusCode}");
            }
          } catch (err) {
            displayError = "Unable to parse the hub's response. Make sure "
                "you typed the hub correctly, try again later, or contact "
                "the hub administrator.";
            error = err.toString();
          }
        }
      }
    }
    if (error.isEmpty) {
      if (global.loginState.hub != hub) {
        error = "B99034 '${global.loginState.hub}'!='$hub'";
      }
    }
    if (error.isEmpty) {
      nextScreen();
      return;
    }
    _log.warning("finished http $hub: $error");
    if (displayError.isEmpty) {
      if (error.startsWith("Failed host lookup:") ||
          error == "Network is unreachable") {
        displayError = 'Cannot find hub "$hub".';
      } else {
        displayError = 'Unable to connect to hub "$hub".';
      }
      displayError += " Make sure that you typed the hub correctly "
          "and that you are connected to the internet.";
    }
    await notificationDialog(
      context: context,
      title: "Unable to connect",
      text: '$displayError (Error "$error".)',
      buttonText: "OK",
    );
    return;
  }

  String? _validateHub(String? value) {
    if (value == null || value.isEmpty) {
      return "Hub is required";
    }
    final hubExp = RegExp(r'^[^\.-].*\..*[^\.-]$');
    if (!hubExp.hasMatch(value)) {
      return 'Hub must contain a "." and begin and end with '
          'a letter or number.';
    }
    return null;
  }

  String? _validateAccount(String? value, String accountKind) {
    if (value == null || value.isEmpty) {
      return "${accountKind.capitalize()} is required";
    }
    if (value.length != accountLen) {
      return "${accountKind.capitalize()} must be "
          "exactly $accountLen characters (including dashes).";
    }
    final hubExp =
        RegExp(r'^[' + base28Digits + r'-]{' + accountLen.toString() + r'}$');
    if (!hubExp.hasMatch(value)) {
      return "Please use only numbers and letters.";
    }
    return null;
  }

  Widget hubTextFormField() => TextFormField(
        restorationId: 'hub_field',
        textInputAction: TextInputAction.next,
        textCapitalization: TextCapitalization.none,
        decoration: InputDecoration(
          filled: true,
          icon: SvgPicture.asset(
            'images/cloud-data-connection.svg',
            width: 30,
            height: 30,
            color: Theme.of(context).colorScheme.primary,
          ),
          hintText: "example.com",
          labelText: "Hub*",
        ),
        initialValue: getHubValue(),
        onSaved: (value) {
          setHubValue(value ?? "");
        },
        validator: _validateHub,
        inputFormatters: <TextInputFormatter>[
          // don't allow upper-case, common symbols except -.
          FilteringTextInputFormatter.deny(
              RegExp(r'''[A-Z~`!@#$%^&\*\(\)_\+=\[\]\{\}\|\\:;"'<>,/\? ]''')),
        ],
      );

  Widget accountTextFormField(String accountKind, String icon,
          {bool isPassword = false}) =>
      TextFormField(
        restorationId: '${accountKind}_field',
        textInputAction: TextInputAction.next,
        textCapitalization: TextCapitalization.characters,
        decoration: InputDecoration(
          filled: true,
          icon: SvgPicture.asset(
            icon,
            width: 30,
            height: 30,
            color: Theme.of(context).colorScheme.primary,
          ),
          hintText: "xxxx-xxxxx-xxxx-xxxxx",
          labelText: "$accountKind*".capitalize(),
          suffixIcon: isPassword
              ? IconButton(
                  icon: Icon(
                      _isObscure ? Icons.visibility : Icons.visibility_off),
                  onPressed: () {
                    setState(() {
                      _isObscure = !_isObscure;
                    });
                  })
              : null,
        ),
        obscureText: isPassword && _isObscure,
        autofocus: getHubValue().isNotEmpty,
        initialValue: getAccountValue(),
        onSaved: (value) {
          setAccountValue(value ?? "");
        },
        maxLength: accountLen,
        maxLengthEnforcement: MaxLengthEnforcement.none,
        validator: (value) => _validateAccount(value, accountKind),
        inputFormatters: <TextInputFormatter>[
          _accountFormatter,
        ],
      );

  Future<void> notificationDialog({
    required BuildContext context,
    String title = "",
    String text = "",
    required String buttonText,
    Stream<String>? messages,
    // warning: Markdown lists cause a crash: https://github.com/flutter/flutter/issues/114748
  }) =>
      showDialog(
          context: context,
          barrierDismissible: false,
          builder: (BuildContext context) => AlertDialog(
                title: Text(title),
                content: messages == null
                    ? Text(text)
                    : StreamBuilder<String>(
                        stream: messages,
                        builder: (context, snapshot) {
                          if (snapshot.connectionState ==
                              ConnectionState.waiting) {
                            return const Text("");
                          } else if (snapshot.connectionState ==
                              ConnectionState.done) {
                            return const Text("");
                          } else if (snapshot.hasError) {
                            return const Text("error 5007");
                          } else {
                            return textMd(context, snapshot.data ?? "");
                          }
                        }),
                actions: <Widget>[
                  TextButton(
                    style: TextButton.styleFrom(
                      textStyle: Theme.of(context).textTheme.labelLarge,
                    ),
                    onPressed: () {
                      _log.fine("TextButton '$buttonText' onPressed()");
                      Navigator.of(context).pop(context);
                    },
                    child: Text(buttonText),
                  )
                ],
              ));

  Future<String?> promptDialog({
    required BuildContext context,
    String? title,
    String? text,
    String? labelText,
    required String buttonText,
    String? cancelButtonText,
  }) async {
    String? response;
    await showDialog(
        context: context,
        barrierDismissible: false,
        builder: (BuildContext context) => AlertDialog(
              title: title == null ? null : Text(title),
              content: SingleChildScrollView(
                child: Form(
                  key: dialogFormKey,
                  child: SizedBox(
                    width: double.infinity,
                    child: Column(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        if (text != null) textMd(context, text),
                        if (text != null) const SizedBox(height: 12),
                        TextFormField(
                          keyboardType: TextInputType.text,
                          decoration: InputDecoration(
                            filled: true,
                            labelText: labelText,
                            suffixIcon: Container(
                              margin: const EdgeInsets.all(8.0),
                              child: SvgPicture.asset(
                                'images/router.svg',
                                width: 30,
                                height: 30,
                                color: Theme.of(context).colorScheme.primary,
                              ),
                            ),
                          ),
                          onFieldSubmitted: (value) {
                            response = value;
                            Navigator.of(context).pop(context);
                          },
                          onSaved: (value) {
                            response = value;
                          },
                        )
                      ],
                    ),
                  ),
                ),
              ),
              actions: <Widget>[
                if (cancelButtonText != null)
                  TextButton(
                    style: TextButton.styleFrom(
                      textStyle: Theme.of(context).textTheme.labelLarge,
                    ),
                    onPressed: () {
                      _log.fine("cancel TextButton '$cancelButtonText' "
                          "onPressed()");
                      // don't save; return null
                      Navigator.of(context).pop(context);
                    },
                    child: Text(cancelButtonText),
                  ),
                TextButton(
                  style: TextButton.styleFrom(
                    textStyle: Theme.of(context).textTheme.labelLarge,
                  ),
                  onPressed: () {
                    _log.fine("okay TextButton '$cancelButtonText' "
                        "onPressed()");
                    dialogFormKey.currentState!.save();
                    Navigator.of(context).pop(context);
                  },
                  child: Text(buttonText),
                ),
              ],
            ));
    return response;
  }
}
