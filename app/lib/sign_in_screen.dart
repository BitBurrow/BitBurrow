import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'package:go_router/go_router.dart';
import 'package:flutter_svg/flutter_svg.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart' as storage;
import 'package:logging/logging.dart';
import 'dart:convert' as convert;
import 'dart:math';
import 'global.dart' as global;
import 'main.dart';
import 'parent_form_state.dart';

final _log = Logger('sign_in_screen');

class SignInScreen extends StatelessWidget {
  const SignInScreen({Key? key}) : super(key: key);

  @override
  Widget build(BuildContext context) => ourScreenLayout(
        context,
        const SignInForm(),
      );
}

class SignInForm extends ParentForm {
  const SignInForm({Key? key}) : super(key: key);

  @override
  SignInFormState createState() => SignInFormState();
}

class SignInFormState extends ParentFormState {
  @override
  void initState() {
    super.initState();
    // check our login state before drawing the screen
    WidgetsBinding.instance
        .addPostFrameCallback((_) => checkLoginState(context));
  }

  @override
  String get restorationId => 'sign_in_form';

  @override
  Future<http.Response?> callApi() {
    String domain = '${global.loginState.hub}:8443';
    String path = '/v1/managers/${global.loginState.pureLoginKey}/servers';
    _log.info("GET https $domain$path");
    return http.get(Uri.https(domain, path));
  }

  @override
  String statusCodeCheck(status) {
    var displayError = statusCodeMessage(status, item: "login key");
    global.loginState.loginKeyVerified = displayError.isEmpty;
    const keyStore = storage.FlutterSecureStorage();
    // if loginState.saveLoginKey == false, values have already been cleared
    if (global.loginState.saveLoginKey && global.loginState.loginKeyVerified) {
      // only save login key if user opts in AND login key is valid
      _log.info("Save to secure storage: hub ${global.loginState.hub}");
      keyStore.write(key: 'hub', value: global.loginState.hub);
      _log.info("Save to secure storage: "
          "login key ${global.loginState.loginKey}");
      keyStore.write(key: 'login_key', value: global.loginState.loginKey);
      _log.info("Save to secure storage: verification state 'true'");
      keyStore.write(key: 'login_key_verified', value: 'true');
    }
    return displayError;
  }

  @override
  String processApiResponse(response) {
    global.loginState.servers =
        convert.jsonDecode(response.body) as List<dynamic>;
    return "";
  }

  @override
  nextScreen() => context.push('/servers');

  @override
  String getHubValue() => global.loginState.hub;

  @override
  void setHubValue(value) {
    global.loginState.hub = value;
  }

  @override
  String getAccountValue() => global.loginState.loginKey;

  @override
  void setAccountValue(value) {
    global.loginState.loginKey = value;
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
          restorationId: 'sign_in_screen_scroll_view',
          padding: const EdgeInsets.symmetric(horizontal: 34),
          controller: scrollController,
          child: Column(
            children: [
              // center elements vertically if less than screen height
              Container(
                  // fixme: replace 600 with the actual height of widgets below
                  height:
                      max((MediaQuery.of(context).size.height - 600) / 2, 0)),
              sizedBoxSpace,
              const FractionallySizedBox(
                widthFactor: 0.8,
                child: Text(
                  "Sign in",
                  textAlign: TextAlign.center,
                  textScaleFactor: 1.8,
                  style: TextStyle(fontWeight: FontWeight.bold),
                ),
              ),
              sizedBoxSpace,
              FractionallySizedBox(
                widthFactor: 0.4,
                child: Image.asset('images/brass-1293947.png'),
              ),
              sizedBoxSpace,
              hubTextFormField(),
              sizedBoxSpace,
              accountTextFormField(
                "login key",
                'images/key.svg',
                isPassword: true, // communicate to user to keep it private
              ),
              // sizedBoxSpace, // Login Key length display substitutes for this
              Row(
                crossAxisAlignment: CrossAxisAlignment.start, // top-align
                children: [
                  Column(
                    children: [
                      const SizedBox(height: 12),
                      SvgPicture.asset(
                        'images/device-floppy.svg',
                        width: 30,
                        height: 30,
                        color: Theme.of(context).colorScheme.primary,
                      ),
                    ],
                  ),
                  const SizedBox(width: 16),
                  Expanded(
                    child: Column(
                      children: [
                        CheckboxListTile(
                          title: textMd(
                              context, "Store my login key on this device"),
                          controlAffinity: ListTileControlAffinity.leading,
                          contentPadding: EdgeInsets.zero,
                          dense: true,
                          value: global.loginState.saveLoginKey,
                          onChanged: (value) {
                            setState(() {
                              global.loginState.saveLoginKey = value == true;
                            });
                          },
                        ),
                      ],
                    ),
                  ),
                ],
              ),
              sizedBoxSpace,
              Center(
                child: ElevatedButton(
                  onPressed: () {
                    _log.fine("ElevatedButton 'SIGN IN' onPressed()");
                    return signIn();
                  },
                  child: const Text("SIGN IN"),
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

  void signIn() {
    if (global.loginState.saveLoginKey == false) {
      // if box not checked, clear stored login key even before trying server
      clearStoredLoginKey();
    }
    var err = "";
    if (validateTextFields()) {
      err = "Please fix the errors in red before submitting.";
    } else {
      return handleSubmitted();
    }
    showInSnackBar(err);
  }

  void checkLoginState(context) {
    if (GoRouter.of(context).location == '/forget-login-key') {
      // going to a different screen and then to '/sign-in' is the only reliable way
      // ... I could find to clear fields if user is already on SignInScreen
      SignInFormState.clearStoredLoginKey();
      global.loginState.saveLoginKey = false;
      global.loginState.loginKey = '';
      global.loginState.loginKeyVerified = false;
      GoRouter.of(context).go('/sign-in');
    }
    // if login key is verified, press the sign-in button (virtually); this
    // ... is similar to a redirect but if login fails will stay on this screen
    if (global.loginState.loginKeyVerified) {
      signIn();
    }
  }

  static void clearStoredLoginKey() {
    const keyStore = storage.FlutterSecureStorage();
    _log.info("Clear stored login key from secure storage");
    // no need: keyStore.write(key: 'hub', value: '');
    keyStore.write(key: 'login_key', value: '');
    keyStore.write(key: 'login_key_verified', value: 'false');
  }
}
