import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:flutter_svg/flutter_svg.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart' as storage;
import 'package:logging/logging.dart';
import 'dart:math';
import 'main.dart';
import 'parent_form_state.dart';
import 'hub_rpc.dart';

final _log = Logger('sign_in_screen');
var loginState = LoginState.instance;

class SignInScreen extends StatelessWidget {
  const SignInScreen({super.key});

  @override
  Widget build(BuildContext context) => ourScreenLayout(
        context,
        const SignInForm(),
      );
}

class SignInForm extends ParentForm {
  const SignInForm({super.key});

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
  String get lkocc => "login key";

  @override
  Future<void> callApi() async {
    loginState.loginKeyVerified = false; // in case an exception is thrown
    final rpc = HubRpc.instance;
    var response = await rpc.sendRequest(
      'list_bases',
      {'login_key': loginState.pureLoginKey},
    );
    loginState.loginKeyVerified = true; // no exceptions ∴ hub said it's valid
    const keyStore = storage.FlutterSecureStorage();
    // if loginState.saveLoginKey == false, values have already been cleared
    if (loginState.saveLoginKey) {
      // only save login key if user opts in AND login key is valid
      _log.info("Save to secure storage: hub ${loginState.hub}");
      keyStore.write(key: 'hub', value: loginState.hub);
      _log.info("Save to secure storage: "
          "login key ${loginState.loginKey}");
      keyStore.write(key: 'login_key', value: loginState.loginKey);
      _log.info("Save to secure storage: verification state 'true'");
      keyStore.write(key: 'login_key_verified', value: 'true');
    }
    loginState.bases = response as List<dynamic>;
  }

  @override
  nextScreen() => context.push('/bases');

  @override
  String getHubValue() => loginState.hub;

  @override
  void setHubValue(value) {
    loginState.hub = value;
  }

  @override
  String getAccountValue() => loginState.loginKey;

  @override
  void setAccountValue(value) {
    loginState.loginKey = value;
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
                  textScaler: TextScaler.linear(1.8),
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
                        colorFilter: ColorFilter.mode(
                            Theme.of(context).colorScheme.primary,
                            BlendMode.srcIn),
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
                          value: loginState.saveLoginKey,
                          onChanged: (value) {
                            setState(() {
                              loginState.saveLoginKey = value == true;
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
                style: Theme.of(context).textTheme.bodySmall,
              ),
              sizedBoxSpace,
            ],
          ),
        ),
      ),
    );
  }

  void signIn() {
    if (loginState.saveLoginKey == false) {
      // if box not checked, clear stored login key even before trying hub
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
    if (GoRouterState.of(context).path == '/forget-login-key') {
      // going to a different screen and then to '/sign-in' is the only reliable way
      // ... I could find to clear fields if user is already on SignInScreen
      SignInFormState.clearStoredLoginKey();
      loginState.saveLoginKey = false;
      loginState.loginKey = '';
      loginState.loginKeyVerified = false;
      GoRouter.of(context).go('/sign-in');
    }
    // if login key is verified, press the sign-in button (virtually); this
    // ... is similar to a redirect but if login fails will stay on this screen
    if (loginState.loginKeyVerified) {
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
