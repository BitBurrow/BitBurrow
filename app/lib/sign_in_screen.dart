import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'dart:convert' as convert;
import 'dart:math';
import 'main.dart';

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
  String get restorationId => 'sign_in_form';

  @override
  Future<http.Response?> callApi() => http.get(Uri.http(
        '${loginState.hub}:8443',
        '/v1/accounts/${loginState.loginKey}/servers',
      ));

  @override
  bool statusCodeIsOkay(status) {
    loginState.loginKeyVerified = status == 200;
    return status == 200;
  }

  @override
  String processApiResponse(response) {
    final jsonResponse =
        convert.jsonDecode(response.body)['servers'] as List<dynamic>;
    loginState.servers = List<int>.from(jsonResponse);
    return "";
  }

  @override
  String nextScreenUrl() => '/servers';

  @override
  String getRestorationId() => 'sign_in_screen_scroll_view';

  @override
  String getHubValue() => loginState.hub;

  @override
  void setHubValue(value) {
    loginState.hub = value;
  }

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
        child: SingleChildScrollView(
          restorationId: getRestorationId(),
          padding: const EdgeInsets.symmetric(horizontal: 16),
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
                  "Sign in:",
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
              accountTextFormField("login key", 'images/key.svg'),
              sizedBoxSpace,
              Center(
                child: ElevatedButton(
                  onPressed: handleSubmitted,
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
}
