import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:http/http.dart' as http;
import 'package:flutter_svg/flutter_svg.dart';
import 'main.dart';

//                                               123456789012345678
// Strip illegal chars, format incoming text as: XXX-XXXX-XXX-XXXXX
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
      if (l == 3 || l == 8 || l == 12) after.write('-');
    }
    if (beforeLength == beforePos) afterPos = after.length;
    return TextEditingValue(
      text: after.toString(),
      selection: TextSelection.collapsed(offset: afterPos),
    );
  }
}

abstract class ParentFormState extends State<ParentForm> with RestorationMixin {
  // based on https://github.com/flutter/gallery/blob/d030f1e5316310c48fc725f619eb980a0597366d/lib/demos/material/text_field_demo.dart
  bool _isObscure = true;
  final scrollController = ScrollController();

  void showInSnackBar(String value) {
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
    var connectingDialog = showPopupDialog(
      context: context,
      title: "Connecting to hub ...",
      buttonText: "CANCEL",
    );
    var dialogState = DialogStates.open;
    connectingDialog.whenComplete(() {
      if (dialogState == DialogStates.open) {
        print("user canceled dialog before request completed");
        dialogState = DialogStates.canceled;
      } else {
        dialogState = DialogStates.closed;
      }
    });
    var futureDelay = Future.delayed(const Duration(seconds: 1), () {});
    print("calling http $hub ...");
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
      print("(finished http $hub but ignoring because it was user-canceled)");
      return;
    }
    if (!mounted) {
      print("B25600 finished http $hub but !mounted");
      return;
    }
    dialogState = DialogStates.closing;
    Navigator.pop(context); // close dialog
    var displayError = "";
    if (error.isEmpty) {
      if (response == null) {
        error = "B29348";
      } else {
        displayError = validateStatusCode(response.statusCode);
        if (displayError.isNotEmpty) {
          error = "invalid status code ${response.statusCode}";
        } else {
          // status code is okay
          try {
            error = processApiResponse(response);
            if (error.isNotEmpty) {
              displayError = "Received invalid data from the hub. Contact the "
                  "hub administrator.";
            } else {
              print("successful connection to $hub, "
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
      if (loginState.hub != hub) {
        error = "B99034 '${loginState.hub}'!='$hub'";
      }
    }
    if (error.isEmpty) {
      nextScreen();
      return;
    }
    print("finished http $hub: $error");
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
    await showPopupDialog(
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
    if (value.length != 18) {
      return "${accountKind.capitalize()} must be "
          "exactly 18 characters (including dashes).";
    }
    final hubExp = RegExp(r'^[' + base28Digits + r'-]{18}$');
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
          hintText: "xxx-xxxx-xxx-xxxxx",
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
        maxLength: 18,
        maxLengthEnforcement: MaxLengthEnforcement.none,
        validator: (value) => _validateAccount(value, accountKind),
        inputFormatters: <TextInputFormatter>[
          _accountFormatter,
        ],
      );
}
