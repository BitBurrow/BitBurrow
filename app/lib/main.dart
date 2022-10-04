// ignore_for_file: avoid_print

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_markdown/flutter_markdown.dart';
import 'package:http/http.dart' as http;
import 'package:url_launcher/url_launcher.dart';
import 'package:go_router/go_router.dart';
import 'package:flutter_svg/flutter_svg.dart';
import 'welcome_screen.dart';
import 'new_login_key_screen.dart';
import 'sign_in_screen.dart';
import 'servers_screen.dart';
import 'new_server_screen.dart';

void main() {
  runApp(App());
}

Page<void> ourPageBuilder(
        BuildContext context, GoRouterState state, Widget child) =>
    CustomTransitionPage<void>(
      key: state.pageKey,
      child: child,
      transitionsBuilder: // change the default MaterialApp transition
          (context, animation, secondaryAnimation, child) =>
              FadeTransition(opacity: animation, child: child),
    );

class App extends StatelessWidget {
  App({Key? key}) : super(key: key);

  static const String title = 'BitBurrow';

  static const ourPrimaryColor = 0xff5b5b5b;
  final int r = (ourPrimaryColor & 0x00ff0000) ~/ 0x10000;
  final int g = (ourPrimaryColor & 0x0000ff00) ~/ 0x100;
  final int b = (ourPrimaryColor & 0x000000ff);
  late final Map<int, Color> ourColorCodes = {
    50: Color.fromRGBO(r, g, b, .1),
    100: Color.fromRGBO(r, g, b, .2),
    200: Color.fromRGBO(r, g, b, .3),
    300: Color.fromRGBO(r, g, b, .4),
    400: Color.fromRGBO(r, g, b, .5),
    500: Color.fromRGBO(r, g, b, .6),
    600: Color.fromRGBO(r, g, b, .7),
    700: Color.fromRGBO(r, g, b, .8),
    800: Color.fromRGBO(r, g, b, .9),
    900: Color.fromRGBO(r, g, b, 1),
  };

  @override
  Widget build(BuildContext context) {
    return MaterialApp.router(
      title: title,
      theme: ThemeData(
        primarySwatch:
            MaterialColor(ourPrimaryColor, ourColorCodes), // checkboxes
        colorScheme: ColorScheme.fromSwatch().copyWith(
          primary: const Color(ourPrimaryColor),
          secondary: const Color(0xffd3a492),
          background: const Color(0xffff0000), // seems unused
        ),
      ),
      routeInformationProvider: _router.routeInformationProvider,
      routeInformationParser: _router.routeInformationParser,
      routerDelegate: _router.routerDelegate,
    );
  }

  final GoRouter _router = GoRouter(
    routes: <GoRoute>[
      GoRoute(
        path: '/',
        pageBuilder: (context, state) =>
            ourPageBuilder(context, state, const WelcomeScreen()),
        routes: <GoRoute>[
          // has back arrow to above page
          GoRoute(
            path: 'new-login-key',
            pageBuilder: (context, state) =>
                ourPageBuilder(context, state, const NewLoginKeyScreen()),
          ),
          GoRoute(
            path: 'sign-in',
            pageBuilder: (context, state) =>
                ourPageBuilder(context, state, const SignInScreen()),
          ),
          GoRoute(
            path: 'servers',
            pageBuilder: (context, state) =>
                ourPageBuilder(context, state, const ServersScreen()),
          ),
          GoRoute(
            path: 'new-server',
            pageBuilder: (context, state) =>
                ourPageBuilder(context, state, const NewServerScreen()),
          ),
        ],
      ),
    ],
    urlPathStrategy:
        UrlPathStrategy.path, // turn off the extra `#/` in the URLs
  );
}

MarkdownBody textMd(BuildContext context, md) {
  return MarkdownBody(
    selectable:
        false, // DO NOT USE; see https://stackoverflow.com/questions/73491527
    styleSheet: MarkdownStyleSheet.fromTheme(Theme.of(context)).copyWith(
      textScaleFactor: 1.15, // match other text
      a: const TextStyle(
        color: Color.fromARGB(255, 40, 128, 22),
        decoration: TextDecoration.underline,
      ),
    ),
    onTapLink: (text, url, title) {
      if (url == null) return;
      if (url[0] == '/') {
        context.push(url); // within our app
      } else {
        launchUrl(Uri.parse(url));
      }
    },
    data: md,
  );
}

Future showSimpleDialog(
        BuildContext context, String title, String text, String buttonText) =>
    showDialog(
        context: context,
        barrierDismissible: false,
        builder: (BuildContext context) => AlertDialog(
              title: Text(title),
              content: Text(text),
              actions: <Widget>[
                TextButton(
                  style: TextButton.styleFrom(
                    textStyle: Theme.of(context).textTheme.labelLarge,
                  ),
                  onPressed: () {
                    Navigator.of(context).pop(context);
                  },
                  child: Text(buttonText),
                )
              ],
            ));

Widget ourScreenLayout(BuildContext context, Widget body,
        {Widget? floatingActionButton}) =>
    Scaffold(
      appBar: AppBar(
          // title: const Text(App.title),
          toolbarHeight: 40.0,
          actions: <Widget>[
            if (GoRouter.of(context).location != '/sign-in' &&
                loginState.isNotSignedIn())
              IconButton(
                  icon: const Icon(Icons.login),
                  tooltip: "Sign in",
                  onPressed: () {
                    context.push('/sign-in');
                  }),
            IconButton(
                icon: const Icon(Icons.more_vert),
                tooltip: "More",
                onPressed: () {}),
          ]),
      body: Padding(
        padding: const EdgeInsets.all(18.0),
        child: body,
      ),
      floatingActionButton: floatingActionButton,
    );

extension StringExtension on String {
  String capitalize() {
    return "${this[0].toUpperCase()}${substring(1).toLowerCase()}";
  }
}

class LoginState {
  String hub = "";
  String coupon = "";
  String newLoginKey = "";
  String loginKey = "";
  bool loginKeyVerified = false;
  List<int> servers = [];

  bool isSignedIn() => loginKeyVerified;
  bool isNotSignedIn() => !loginKeyVerified;
}

var loginState = LoginState();

abstract class ParentForm extends StatefulWidget {
  const ParentForm({Key? key}) : super(key: key);
}

const String base28Digits = '23456789BCDFGHJKLMNPQRSTVWXZ';

enum DialogStates {
  open,
  closing,
  closed,
  canceled,
}

abstract class ParentFormState extends State<ParentForm> with RestorationMixin {
  // based on https://github.com/flutter/gallery/blob/d030f1e5316310c48fc725f619eb980a0597366d/lib/demos/material/text_field_demo.dart
  bool _isObscure = true;

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
  bool statusCodeIsOkay(status);
  String processApiResponse(response);
  String nextScreenUrl();
  String getRestorationId();
  String getHubValue();
  void setHubValue(String value);
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
    var connectingDialog =
        showSimpleDialog(context, "Connecting to hub ...", "", "CANCEL");
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
      print("(finished http $hub but !mounted)");
      return;
    }
    dialogState = DialogStates.closing;
    Navigator.pop(context); // close dialog
    var displayError = "";
    if (error.isEmpty) {
      if (response == null) {
        error = "B29348";
      } else {
        if (!statusCodeIsOkay(response.statusCode)) {
          displayError = "The hub responseded with an invalid status code. "
              "Make sure you typed the hub correctly, try again later, or "
              "contact the hub administrator.";
          error = "invalid status code ${response.statusCode}";
        } else {
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
      context.push(nextScreenUrl());
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
    await showSimpleDialog(
      context,
      "Unable to connect",
      '$displayError (Error "$error".)',
      "OK",
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
