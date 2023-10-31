import 'dart:async' as dasync;
import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'package:go_router/go_router.dart';
import 'package:flutter_svg/flutter_svg.dart';
import 'package:stream_channel/stream_channel.dart' as sc;
import 'package:json_rpc_2/json_rpc_2.dart' as jsonrpc;
import 'package:logging/logging.dart';
import 'dart:convert' as convert;
import 'dart:math';
import 'main.dart';
import 'parent_form_state.dart';
import 'persistent_websocket.dart';

final _log = Logger('welcome_screen');
var loginState = LoginState.instance;

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
    try {
      var url = Uri(
              scheme: 'wss',
              host: loginState.hub,
              port: 8443,
              path: '/rpc1/${loginState.pureCoupon}/4')
          .toString();
      _log.info("connecting to $url");
      final hubMessages = PersistentWebSocket('');
      hubMessages.connect(url).onError((err, stackTrace) {
        _log.warning("B17834 pws: $err");
      });
      var channel = sc.StreamChannel(
          hubMessages.stream
              .asyncMap((data) => convert.utf8.decode(List<int>.from(data))),
          hubMessages.sink);
      var rpc = jsonrpc.Peer(channel.cast<String>());
      rpc.sendRequest(
          'test_call', {'word': 'déjà vus', 'number': 3, 'emoji': '❤️☕🙃'});
    } catch (err) {
      _log.warning("B40125 pws: $err");
    }
    String domain = '${loginState.hub}:8443';
    String path = '/v1/coupons/${loginState.pureCoupon}/managers';
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
    loginState.newLoginKey = loginState.dressLoginKey(pureLoginKey);
    loginState.loginKey = ''; // force user to type it
    loginState.loginKeyVerified = false;
    return "";
  }

  @override
  nextScreen() => context.push('/new-login-key');

  @override
  String getHubValue() => loginState.hub;

  @override
  void setHubValue(value) {
    loginState.hub = value;
  }

  @override
  String getAccountValue() => "";

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
