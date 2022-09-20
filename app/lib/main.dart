// ignore_for_file: avoid_print

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_markdown/flutter_markdown.dart';
import 'package:http/http.dart' as http;
import 'package:url_launcher/url_launcher.dart';
import 'package:go_router/go_router.dart';
import 'package:flutter_svg/flutter_svg.dart';
import 'dart:convert' as convert;
import 'dart:math';

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

  @override
  Widget build(BuildContext context) {
    return MaterialApp.router(
      title: title,
      theme: ThemeData(
        colorScheme: ColorScheme.fromSwatch().copyWith(
          primary: const Color(0xFF343A40),
          secondary: const Color(0xFFFFC107),
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
        ],
      ),
      GoRoute(
        path: '/page3',
        pageBuilder: (context, state) =>
            ourPageBuilder(context, state, const Page3Screen()),
      ),
    ],
    urlPathStrategy:
        UrlPathStrategy.path, // turn off the extra `#/` in the URLs
  );
}

void onMarkdownClick(BuildContext context, String url) {
  if (url[0] == '/') {
    // within our app
    context.go(url);
  } else {
    launchUrl(Uri.parse(url));
  }
}

MarkdownBody textMd(BuildContext context, md) {
  return MarkdownBody(
    selectable:
        false, // DO NOT USE; see https://stackoverflow.com/questions/73491527
    // fixme: read from a file: https://developer.school/tutorials/how-to-display-markdown-in-flutter
    styleSheet: MarkdownStyleSheet.fromTheme(ThemeData(
        textTheme: const TextTheme(
            bodyText2: TextStyle(
      fontSize: 16.0,
      color: Colors.black,
    )))),
    onTapLink: (text, url, title) {
      onMarkdownClick(context, url!);
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

Widget ourScreenLayout(BuildContext context, Widget child) => Scaffold(
      appBar: AppBar(
        // title: const Text(App.title),
        toolbarHeight: 40.0,
      ),
      body: SingleChildScrollView(
          child: Padding(
        padding: const EdgeInsets.all(18.0),
        child: child,
      )),
    );

class WelcomeScreen extends StatelessWidget {
  const WelcomeScreen({Key? key}) : super(key: key);

  @override
  Widget build(BuildContext context) => ourScreenLayout(
        context,
        const WelcomeForm(),
      );
}

class WelcomeForm extends StatefulWidget {
  const WelcomeForm({Key? key}) : super(key: key);

  @override
  WelcomeFormState createState() => WelcomeFormState();
}

class LoginState {
  String hub = "";
  String coupon = "";
  String newLoginKey = "";
  String loginKey = ""; // if not empty, user is logged in (client side)
}

LoginState loginState = LoginState();

const String base28Digits = '23456789BCDFGHJKLMNPQRSTVWXZ';

enum DialogStates {
  open,
  closing,
  closed,
  canceled,
}

class WelcomeFormState extends State<WelcomeForm> with RestorationMixin {
  // based on https://github.com/flutter/gallery/blob/d030f1e5316310c48fc725f619eb980a0597366d/lib/demos/material/text_field_demo.dart
  late FocusNode _hub, _coupon;

  @override
  void initState() {
    super.initState();
    _hub = FocusNode();
    _coupon = FocusNode();
  }

  @override
  void dispose() {
    _hub.dispose();
    _coupon.dispose();
    super.dispose();
  }

  void showInSnackBar(String value) {
    ScaffoldMessenger.of(context).hideCurrentSnackBar();
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(
      content: Text(value),
    ));
  }

  @override
  String get restorationId => 'welcome_form';

  @override
  void restoreState(RestorationBucket? oldBucket, bool initialRestore) {
    registerForRestoration(_autoValidateModeIndex, 'autovalidate_mode');
  }

  final RestorableInt _autoValidateModeIndex =
      RestorableInt(AutovalidateMode.disabled.index);

  final GlobalKey<FormState> _formKey = GlobalKey<FormState>();
  final _CouponTextInputFormatter _couponFormatter =
      _CouponTextInputFormatter();

  void _handleSubmitted() async {
    final form = _formKey.currentState!;
    if (!form.validate()) {
      _autoValidateModeIndex.value =
          AutovalidateMode.always.index; // Start validating on every change.
      showInSnackBar("Please fix the errors in red before submitting.");
      return;
    }
    form.save();
    var hub = loginState.hub; // keep local value in case dialog is canceled ...
    // and _handleSubmitted() is re-entered
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
    // wait 1 seconds so user will see that something is happening
    await Future.delayed(const Duration(seconds: 1), () {});
    print("calling http $hub ...");
    http.Response? response;
    var errMessage = "";
    try {
      var url =
          Uri.http('$hub:8443', '/v1/accounts/${loginState.coupon}/accounts');
      response = await http.post(url);
    } catch (err) {
      errMessage = err.toString();
    }
    // ignore user-canceled result, successful or not
    if (dialogState == DialogStates.canceled) return;
    if (!mounted) return;
    dialogState = DialogStates.closing;
    Navigator.pop(context); // close dialog
    if (errMessage.startsWith("Failed host lookup:") ||
        errMessage == "Network is unreachable" ||
        errMessage == "Connection timed out" ||
        errMessage == "Connection refused") {
      errMessage = 'Cannot find hub "$hub". Make sure that you typed the hub'
          "correctly and that you are connected to the internet. "
          '(Error "$errMessage".)';
    }
    if (errMessage.isEmpty) {
      if (response == null) {
        errMessage = "Internal error B29348.";
      } else {
        if (response.statusCode != 201) {
          errMessage = "The hub responseded with an invalid status code. "
              "Make sure you typed the hub correctly, try again later, or "
              "contact the hub administrator. "
              '(Error "invalid status code ${response.statusCode}".)';
        } else {
          try {
            var jsonResponse =
                convert.jsonDecode(response.body) as Map<String, dynamic>;
            String? newLoginKey = jsonResponse["login_key"];
            if (newLoginKey == null || newLoginKey.length != 18) {
              errMessage = "Received invalid data from the hub. Contact the "
                  "hub administrator. "
                  '(Error "login_key is $newLoginKey".)';
            } else {
              print("successful connection to $hub, "
                  "status code ${response.statusCode}");
              loginState.newLoginKey = newLoginKey;
            }
          } catch (err) {
            errMessage = "Unable to parse the hub's response. Make sure "
                "you typed the hub correctly, try again later, or contact "
                "the hub administrator. "
                '(Error "$err".)';
          }
        }
      }
    }
    if (errMessage.isEmpty) {
      if (loginState.hub != hub) {
        errMessage = "Internal error B99034."
            '(Error "${loginState.hub} != $hub".)';
      }
    }
    if (errMessage.isEmpty) {
      context.go('/new-login-key');
      return;
    }
    print("finished http $hub: $errMessage");
    await showSimpleDialog(context, "Unable to connect", errMessage, "OK");
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

  String? _validateCoupon(String? value) {
    if (value == null || value.isEmpty) {
      return "Coupon is required";
    }
    if (value.length != 18) {
      return "Coupon must be exactly 18 characters (including dashes).";
    }
    final hubExp = RegExp(r'^[' + base28Digits + r'-]{18}$');
    if (!hubExp.hasMatch(value)) {
      return "Please use only numbers and letters.";
    }
    return null;
  }

  @override
  Widget build(BuildContext context) {
    const sizedBoxSpace = SizedBox(height: 24);

    return Form(
      key: _formKey,
      autovalidateMode: AutovalidateMode.values[_autoValidateModeIndex.value],
      child: Scrollbar(
        child: SingleChildScrollView(
          restorationId: 'welcome_screen_scroll_view',
          padding: const EdgeInsets.symmetric(horizontal: 16),
          child: Column(
            children: [
              // center elements vertically if less than screen height
              Container(
                  // fixme: replace 700 with the actual height of widgets below
                  height:
                      max((MediaQuery.of(context).size.height - 700) / 2, 0)),
              sizedBoxSpace,
              const FractionallySizedBox(
                widthFactor: 0.8,
                child: Text(
                  "Welcome to BitBurrow",
                  textAlign: TextAlign.center,
                  textScaleFactor: 1.8,
                  style: TextStyle(fontWeight: FontWeight.bold),
                ),
              ),
              sizedBoxSpace,
              FractionallySizedBox(
                widthFactor: 0.4,
                child: Image.asset("images/BitBurrow.png"),
              ),
              sizedBoxSpace,
              textMd(
                  context,
                  "This app needs a BitBurrow Hub to run. "
                  "Please enter the information below."),
              sizedBoxSpace,
              TextFormField(
                restorationId: 'hub_field',
                textInputAction: TextInputAction.next,
                textCapitalization: TextCapitalization.none,
                decoration: InputDecoration(
                  filled: true,
                  icon: SvgPicture.asset(
                    'images/cloud-data-connection.svg',
                    width: 30,
                    height: 30,
                    color: Colors.grey[700],
                  ),
                  hintText: "example.com",
                  labelText: "Hub*",
                ),
                onSaved: (value) {
                  loginState.hub = value ?? "";
                  _coupon.requestFocus();
                },
                validator: _validateHub,
                inputFormatters: <TextInputFormatter>[
                  // don't allow upper-case, common symbols except -.
                  FilteringTextInputFormatter.deny(RegExp(
                      r'''[A-Z~`!@#$%^&\*\(\)_\+=\[\]\{\}\|\\:;"'<>,/\? ]''')),
                ],
              ),
              sizedBoxSpace,
              TextFormField(
                restorationId: 'coupon_field',
                textInputAction: TextInputAction.next,
                textCapitalization: TextCapitalization.characters,
                decoration: InputDecoration(
                  filled: true,
                  icon: SvgPicture.asset(
                    'images/ticket.svg',
                    width: 30,
                    height: 30,
                    color: Colors.grey[700],
                  ),
                  hintText: "XXX-XXXX-XXX-XXXXX (Case-insensitive)",
                  labelText: "Coupon*",
                ),
                onSaved: (value) {
                  loginState.coupon = value ?? "";
                },
                maxLength: 18,
                maxLengthEnforcement: MaxLengthEnforcement.none,
                validator: _validateCoupon,
                inputFormatters: <TextInputFormatter>[
                  _couponFormatter,
                ],
              ),
              sizedBoxSpace,
              Center(
                child: ElevatedButton(
                  onPressed: _handleSubmitted,
                  child: const Text("SUBMIT"),
                ),
              ),
              sizedBoxSpace,
              Text(
                "* indicates required field",
                style: Theme.of(context).textTheme.caption,
              ),
              sizedBoxSpace,
            ],
          ),
        ),
      ),
    );
  }
}

//                                               123456789012345678
// Strip illegal chars, format incoming text as: XXX-XXXX-XXX-XXXXX
class _CouponTextInputFormatter extends TextInputFormatter {
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

class NewLoginKeyScreen extends StatelessWidget {
  const NewLoginKeyScreen({Key? key}) : super(key: key);

  @override
  Widget build(BuildContext context) {
    const sizedBoxSpace = SizedBox(height: 24);
    return ourScreenLayout(
      context,
      Scrollbar(
        child: SingleChildScrollView(
          restorationId: 'new_login_key_screen_scroll_view',
          padding: const EdgeInsets.symmetric(horizontal: 16),
          child: Column(
            children: [
              // center elements vertically if less than screen height
              Container(
                  // fixme: replace 550 with the actual height of widgets below
                  height:
                      max((MediaQuery.of(context).size.height - 550) / 2, 0)),
              sizedBoxSpace,
              const FractionallySizedBox(
                widthFactor: 0.8,
                child: Text(
                  "Here is your new login key:",
                  textAlign: TextAlign.center,
                  textScaleFactor: 1.8,
                  style: TextStyle(fontWeight: FontWeight.bold),
                ),
              ),
              sizedBoxSpace,
              FractionallySizedBox(
                widthFactor: 0.6,
                child: SvgPicture.asset("images/padlock-24051.svg"),
              ),
              sizedBoxSpace,
              FractionallySizedBox(
                widthFactor: 0.8,
                child: Text(
                  loginState.newLoginKey,
                  textAlign: TextAlign.center,
                  textScaleFactor: 1.8,
                  style: const TextStyle(fontWeight: FontWeight.normal),
                ),
              ),
              sizedBoxSpace,
              textMd(
                  context,
                  "Before continuing, write this down in a safe place. You "
                  "will need it in the future to make changes to your router "
                  "and VPN settings."),
              sizedBoxSpace,
              sizedBoxSpace,
              Center(
                child: ElevatedButton(
                  onPressed: () {},
                  child: const Text("I HAVE WRITTEN IT DOWN"),
                ),
              ),
              sizedBoxSpace,
            ],
          ),
        ),
      ),
    );
  }
}

class Page3Screen extends StatelessWidget {
  const Page3Screen({Key? key}) : super(key: key);

  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: AppBar(title: const Text(App.title)),
        body: Center(
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: <Widget>[
              ElevatedButton(
                onPressed: () => context.go('/'),
                child: const Text('Go back to home page'),
              ),
            ],
          ),
        ),
      );
}
