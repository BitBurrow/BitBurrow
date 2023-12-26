import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_svg/flutter_svg.dart';
import 'package:logging/logging.dart';
import 'package:json_rpc_2/json_rpc_2.dart' as jsonrpc;
import 'persistent_websocket.dart';
import 'hub_rpc.dart';
import 'main.dart';

final _log = Logger('parent_form_state');
var loginState = LoginState.instance;

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
    _log.fine("showInSnackBar: $value");
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

  String get lkocc; // "login key" or "coupon code"
  Future<void> callApi(); // throws an exception for any error
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
    final rpc = HubRpc.instance;
    var connectingDialog = notificationDialog(
      context: context,
      title: "Connecting to hub ...",
      messages: rpc.err,
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
    String? error;
    String errorSource = "null";
    rpc.err.listen(
      null,
      onError: (err, stacktrace) {
        error = exceptionText(err, null, lkocc);
        errorSource =
            "PWS"; // network error of some sort from PersistentWebSocket
      },
      cancelOnError: true,
    );
    try {
      await callApi();
    } catch (err, stacktrace) {
      if (error == null) {
        // do nothing if error already set by rpc.error.listen(), above
        error = exceptionText(err, stacktrace, lkocc);
        errorSource = "hub"; // error generated by remote hub
      }
    }
    await futureDelay; // ensure user always sees that something is happening
    if (dialogState == DialogStates.canceled) {
      // ignore user-canceled result, successful or not
      _log.info(
          "B41536 finished $hub RPC but ignoring because it was user-canceled");
      return;
    }
    if (!mounted) {
      _log.warning("B25600 finished $hub RPC but !mounted");
      return;
    }
    dialogState = DialogStates.closing;
    Navigator.pop(context); // close dialog
    if (loginState.hub != hub) {
      error = "B99034 '${loginState.hub}'!='$hub'";
    }
    if (error == null) {
      _log.fine("B19624 finished $hub RPC successfully");
      nextScreen();
      return;
    }
    _log.info("B84481 $hub RPC error via $errorSource: $error");
    await notificationDialog(
      context: context,
      title: "Something went wrong",
      text: sentencify(error!),
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
            colorFilter: ColorFilter.mode(
                Theme.of(context).colorScheme.primary, BlendMode.srcIn),
          ),
          hintText: "example.org",
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
            colorFilter: ColorFilter.mode(
                Theme.of(context).colorScheme.primary, BlendMode.srcIn),
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
                                colorFilter: ColorFilter.mode(
                                    Theme.of(context).colorScheme.primary,
                                    BlendMode.srcIn),
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

/// Convert an exception into an error message.
String exceptionText(Object err, StackTrace? stacktrace, lkocc) {
  String error = "";
  if (err is PWUnrecoverableError) {
    PWUnrecoverableError pwException = err as PWUnrecoverableError;
    error = pwException.message;
  } else if (err is jsonrpc.RpcException) {
    error = err.message;
    // // -25718 is arbitrary, matches use in hub/db.py
    // error = error.replaceFirst("JSON-RPC error -25718: ", "");
  } else {
    _log.severe("B18189 unknown exception $err (type ${err.runtimeType})");
    if (stacktrace != null) {
      _log.severe("======= stacktrace:\n$stacktrace");
    }
    var error = err.toString();
    error = error.replaceFirst("Exception: ", "");
  }
  // FIXME: filter displayError for login codes; see pureAccountRE
  return error.replaceFirst(lkoccString, lkocc);
}

/// Make error message more human-readable.
String sentencify(String input) {
  // Example: "B32521 incorrect action;  invalidating"
  // becomes: "Incorrect action. Invalidating. B32521"
  String suffix = "";
  var berrorCodeMatch = RegExp(r'^B\d{5}\s+').firstMatch(input);
  if (berrorCodeMatch != null) {
    var berrorCode = berrorCodeMatch.group(0) ?? "";
    suffix = " ${berrorCode.trim()}";
    input = input.substring(berrorCode.length);
  }
  if (input.isNotEmpty) {
    input = input[0].toUpperCase() + input.substring(1);
  }
  var segments = input.split(';');
  for (int i = 1; i < segments.length; i++) {
    segments[i] = segments[i].trimLeft();
    if (segments[i].isNotEmpty) {
      segments[i] = segments[i][0].toUpperCase() + segments[i].substring(1);
    }
  }
  input = segments.join('. ');
  if (!input.endsWith('.')) {
    input += '.';
  }
  return input + suffix;
}
