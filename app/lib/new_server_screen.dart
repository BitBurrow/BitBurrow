import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'package:flutter_svg/flutter_svg.dart';
import 'dart:convert' as convert;
import 'main.dart';

const textBlob = """
All of these steps should be done at your "VPN home" location.

` `
## 1. Connect your new router to the internet.

* Make a note of the existing set-up in case there is a problem setting 
  up the new router.
* If possible, install the new router *in place of*  the existing one. 
  This will be more reliable in the long run, but it is generally only 
  possible if the existing set-up consists of a modem (DSL, ADSL, cable, 
  fiber, etc.) and a router, connected by an Ethernet cable. Disconnect 
  the Ethernet cable from the existing router and connect it to the WAN 
  jack on your new router. The WAN jack is sometimes labeled "Ethernet 
  In", "Internet", with a globe symbol, or is unlabeled but uniquely 
  colored. [More details.](/one-router-details)
* If you do not have the set-up described above, or you are unsure, 
  then use the Ethernet cable that came with your new router. Connect 
  one end to any of the unused LAN jacks on the existing router. 
  Connect the other end to the WAN jack on your new router. The LAN jacks 
  are sometimes labeled "Ethernet" or "Ethernet out" or simply numbered 
  1, 2, etc. The WAN jack is sometimes labeled "Ethernet In", 
  "Internet", with a globe symbol, or is unlabeled but uniquely colored. 
  [More details.](/two-routers-details)

` `
## 2. Plug your new router into a wall socket.

* Make sure at least one light turns on.
* It may take a few minutes for the WiFi to begin working.

` `
## 3. Connect to the new router via WiFi.
* It is sometimes necessary to turn off mobile data (internet via 
  your cellular provider).
* Enable WiFi if needed and scan for available WiFi networks.
* For the GL-AX1800, the WiFi name will be `GL-AX1800-xxx` or 
  `GL-AX1800-xxx-5G` and the WiFi password written on the bottom of 
  the router ("WiFi Key:").
""";

class NewServerScreen extends StatelessWidget {
  const NewServerScreen({Key? key}) : super(key: key);

  @override
  Widget build(BuildContext context) => ourScreenLayout(
        context,
        const NewServerForm(),
      );
}

class NewServerForm extends ParentForm {
  const NewServerForm({Key? key}) : super(key: key);

  @override
  NewServerFormState createState() => NewServerFormState();
}

class NewServerFormState extends ParentFormState {
  @override
  String get restorationId => 'new_server_form';

  @override
  Future<http.Response?> callApi() => http.post(Uri.http(
        '${loginState.hub}:8443',
        '/v1/accounts/${loginState.loginKey}/servers',
      ));

  @override
  String validateStatusCode(status) {
    if (status == 201) return "";
    if (status == 403) return "Invalid login key. Please sign in again.";
    return "The hub responseded with an invalid status code. "
        "Make sure you typed the hub correctly, try again later, or "
        "contact the hub administrator.";
  }

  @override
  String processApiResponse(response) {
    final jsonResponse =
        convert.jsonDecode(response.body) as Map<String, dynamic>;
    String? sshKey = jsonResponse['ssh_key'];
    int? sshPort = jsonResponse['ssh_port'];
    if (sshKey == null || sshPort == null) {
      return "invalid server response"; // error
    } else {
      // loginState.sshLogin = jsonResponse;
      return "";
    }
  }

  @override
  nextScreen() => bbProxy();

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
    loginState.loginKey = value;
  }

  Future bbProxy() async {}

  @override
  Widget build(BuildContext context) {
    const sizedBoxSpace = SizedBox(height: 24);
    return Form(
        key: formKey,
        autovalidateMode: AutovalidateMode.values[autoValidateModeIndex.value],
        child: Scrollbar(
            controller: scrollController,
            child: SingleChildScrollView(
          restorationId: 'new_server_screen_scroll_view',
          padding: const EdgeInsets.symmetric(horizontal: 34),
              controller: scrollController,
          child: Column(
            children: [
              sizedBoxSpace,
              const FractionallySizedBox(
                widthFactor: 0.8,
                child: Text(
                  "Set up a BitBurrow VPN server",
                  textAlign: TextAlign.center,
                  textScaleFactor: 1.8,
                  style: TextStyle(fontWeight: FontWeight.bold),
                ),
              ),
              sizedBoxSpace,
              FractionallySizedBox(
                widthFactor: 0.6,
                child: SvgPicture.asset("images/server-32983.svg"),
              ),
              sizedBoxSpace,
              textMd(context, textBlob),
              sizedBoxSpace,
              sizedBoxSpace,
              Center(
                child: ElevatedButton(
                  onPressed: handleSubmitted,
                  child: const Text("I HAVE DONE THESE"),
                ),
              ),
              sizedBoxSpace,
            ],
          ),
        )));
  }
}
