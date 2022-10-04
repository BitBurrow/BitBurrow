import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'dart:convert' as convert;
import 'dart:math';
import 'main.dart';

class WelcomeScreen extends StatelessWidget {
  const WelcomeScreen({Key? key}) : super(key: key);

  @override
  Widget build(BuildContext context) => ourScreenLayout(
        context,
        const WelcomeForm(),
      );
}

class WelcomeForm extends ParentForm {
  const WelcomeForm({Key? key}) : super(key: key);

  @override
  WelcomeFormState createState() => WelcomeFormState();
}

class WelcomeFormState extends ParentFormState {
  @override
  String get restorationId => 'welcome_form';

  @override
  Future<http.Response?> callApi() => http.post(Uri.http(
        '${loginState.hub}:8443',
        '/v1/accounts/${loginState.coupon}/accounts',
      ));

  @override
  bool statusCodeIsOkay(status) => status == 201;

  @override
  String processApiResponse(response) {
    var jsonResponse =
        convert.jsonDecode(response.body) as Map<String, dynamic>;
    String? newLoginKey = jsonResponse['login_key'];
    if (newLoginKey == null || newLoginKey.length != 18) {
      return "login_key is $newLoginKey"; // error
    } else {
      loginState.newLoginKey = newLoginKey;
      return "";
    }
  }

  @override
  String nextScreenUrl() => '/new-login-key';

  @override
  String getRestorationId() => 'welcome_screen_scroll_view';

  @override
  String getHubValue() => loginState.hub;

  @override
  void setHubValue(value) {
    loginState.hub = value;
  }

  @override
  void setAccountValue(value) {
    loginState.coupon = value;
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
                  "This app needs a BitBurrow hub to run. "
                  "Please enter the information below."),
              sizedBoxSpace,
              hubTextFormField(),
              sizedBoxSpace,
              accountTextFormField("coupon", 'images/ticket.svg'),
              sizedBoxSpace,
              Center(
                child: ElevatedButton(
                  onPressed: handleSubmitted,
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
