import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'package:go_router/go_router.dart';
import 'package:flutter_svg/flutter_svg.dart';
import 'package:logging/logging.dart';
import 'dart:convert' as convert;
import 'dart:math';
import 'global.dart' as global;
import 'main.dart';
import 'parent_form_state.dart';

final _log = Logger('welcome_screen');

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
  bool? checkboxTrustedHub = false;

  @override
  String get restorationId => 'welcome_form';

  @override
  Future<http.Response?> callApi() {
    String domain = '${global.loginState.hub}:8443';
    String path = '/v1/coupons/${global.loginState.pureCoupon}/managers';
    _log.info("POST https $domain$path");
    return http.post(Uri.https(domain, path));
  }

  @override
  String statusCodeCheck(status) => statusCodeMessage(
        status,
        expected: 201,
        item: "coupon",
        fullItem: "coupon code",
      );

  @override
  String processApiResponse(response) {
    final jsonResponse =
        convert.jsonDecode(response.body) as Map<String, dynamic>;
    String? pureLoginKey = jsonResponse['login_key'];
    // API response is without the 3 dashes
    if (pureLoginKey == null || pureLoginKey.length != accountLen - 3) {
      return "login_key is $pureLoginKey"; // error
    }
    global.loginState.newLoginKey =
        global.loginState.dressLoginKey(pureLoginKey);
    global.loginState.loginKey = ''; // force user to type it
    global.loginState.loginKeyVerified = false;
    return "";
  }

  @override
  nextScreen() => context.push('/new-login-key');

  @override
  String getHubValue() => global.loginState.hub;

  @override
  void setHubValue(value) {
    global.loginState.hub = value;
  }

  @override
  String getAccountValue() => "";

  @override
  void setAccountValue(value) {
    global.loginState.coupon = value;
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
          restorationId: 'welcome_screen_scroll_view',
          padding: const EdgeInsets.symmetric(horizontal: 34),
          controller: scrollController,
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
                child: Image.asset('images/BitBurrow.png'),
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
              // sizedBoxSpace, // Coupon length display substitutes for this
              Row(
                crossAxisAlignment: CrossAxisAlignment.start, // top-align
                children: [
                  Column(
                    children: [
                      const SizedBox(height: 12),
                      SvgPicture.asset(
                        'images/user-check.svg',
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
                          title: textMd(context, "I trust this hub.*"),
                          controlAffinity: ListTileControlAffinity.leading,
                          contentPadding: EdgeInsets.zero,
                          dense: true,
                          value: checkboxTrustedHub,
                          onChanged: (value) {
                            setState(() {
                              checkboxTrustedHub = value;
                            });
                          },
                        ),
                        textMd(
                            context,
                            "(The person or entity that controls the "
                            "BitBurrow hub specified above, when used with "
                            "this app, can take over your router, snoop on "
                            "your internet traffic, and attack other devices "
                            "on your local network.)"),
                      ],
                    ),
                  ),
                ],
              ),
              sizedBoxSpace,
              Center(
                child: ElevatedButton(
                  onPressed: () {
                    _log.fine("ElevatedButton 'SUBMIT' onPressed()");
                    var err = "";
                    if (validateTextFields()) {
                      err = "Please fix the errors in red before submitting.";
                    } else if (checkboxTrustedHub != true) {
                      err = "Please check the 'I trust this hub' box before "
                          "submitting.";
                    } else {
                      return handleSubmitted();
                    }
                    showInSnackBar(err);
                  },
                  child: const Text("SUBMIT"),
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
}
