import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'package:go_router/go_router.dart';
import 'package:flutter_svg/flutter_svg.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart' as storage;
import 'package:logging/logging.dart';
import 'dart:convert' as convert;
import 'dart:math';
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
    String domain = '${loginState.hub}:8443';
    String path = '/v1/accounts/${loginState.pureLoginKey}/servers';
    _log.info("GET http $domain$path");
    return http.get(Uri.http(domain, path));
  }

  @override
  String validateStatusCode(status) {
    bool serverError;
    if (status == 200 || status == 403) {
      loginState.loginKeyVerified = status == 200;
      serverError = false;
    } else {
      serverError = true;
    }
    const keyStore = storage.FlutterSecureStorage();
    // if loginState.saveLoginKey == false, values have already been cleared
    if (loginState.saveLoginKey && loginState.loginKeyVerified) {
      // only save login key if user opts in AND login key is valid
      _log.info("Saving to secure storage: hub ${loginState.hub}");
      keyStore.write(key: 'hub', value: loginState.hub);
      _log.info("Saving to secure storage: login key ${loginState.loginKey}");
      keyStore.write(key: 'login_key', value: loginState.loginKey);
      _log.info("Saving to secure storage: verification state 'true'");
      keyStore.write(key: 'login_key_verified', value: 'true');
    }
    if (serverError) {
      return "The hub responseded with an invalid status code. "
          "Make sure you typed the hub correctly, try again later, or "
          "contact the hub administrator.";
    } else {
      if (loginState.loginKeyVerified) {
        return "";
      } else {
        return "Invalid login key. Please check what you typed "
            "and try again.";
      }
    }
  }

  @override
  String processApiResponse(response) {
    final jsonResponse =
        convert.jsonDecode(response.body)['servers'] as List<dynamic>;
    loginState.servers = List<int>.from(jsonResponse);
    return "";
  }

  @override
  nextScreen() => context.push('/servers');

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
    if (loginState.saveLoginKey == false) {
      const keyStore = storage.FlutterSecureStorage();
      // if box not checked, clear stored login key even before trying server
      // no need: keyStore.write(key: 'hub', value: '');
      keyStore.write(key: 'login_key', value: '');
      keyStore.write(key: 'login_key_verified', value: 'false');
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
    // if login key is verified, press the sign-in button (virtually)
    if (loginState.loginKeyVerified) {
      signIn();
    }
  }
}
