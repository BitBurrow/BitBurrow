// ignore_for_file: prefer_const_constructors, prefer_const_literals_to_create_immutables

import 'package:flutter/material.dart';
import 'package:flutter_svg/flutter_svg.dart';
import 'package:go_router/go_router.dart';
import 'package:logging/logging.dart';
import 'dart:math';
import 'main.dart';
import 'parent_form_state.dart';

final _log = Logger('bases_screen');
var loginState = LoginState.instance;

class BasesScreen extends StatelessWidget {
  const BasesScreen({super.key});

  @override
  Widget build(BuildContext context) => const BasesForm();
}

class BasesForm extends ParentForm {
  const BasesForm({super.key});

  @override
  BasesFormState createState() => BasesFormState();
}

class BasesFormState extends ParentFormState {
  @override
  String get restorationId => 'bases_form';

  @override
  String get lkocc => "null";

  @override
  Future<void> callApi() async {}

  @override
  nextScreen() => context.push('/new-base');

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

  @override
  Widget build(BuildContext context) {
    const sizedBoxSpace = SizedBox(height: 18);
    return ourScreenLayout(
      context,
      Form(
        key: formKey,
        autovalidateMode: AutovalidateMode.values[autoValidateModeIndex.value],
        child: Column(
          children: [
            sizedBoxSpace,
            FractionallySizedBox(
              widthFactor: 0.8,
              child: loginState.isNotSignedIn()
                  ? Text("You need to sign in.",
                      textAlign: TextAlign.center,
                      textScaler: TextScaler.linear(1.8),
                      style: TextStyle(
                        fontStyle: FontStyle.italic,
                        color: Theme.of(context).colorScheme.surface,
                      ))
                  : loginState.bases.isEmpty
                      ? Text("You have no VPN bases set up.",
                          textAlign: TextAlign.center,
                          textScaler: TextScaler.linear(1.8),
                          style: TextStyle(
                            fontStyle: FontStyle.italic,
                            color: Theme.of(context).colorScheme.surface,
                          ))
                      : Text(
                          "Your VPN bases",
                          textAlign: TextAlign.center,
                          textScaler: TextScaler.linear(1.8),
                          style: TextStyle(fontWeight: FontWeight.bold),
                        ),
            ),
            sizedBoxSpace,
            Expanded(
              child: Center(
                child: SizedBox(
                  width: min(MediaQuery.of(context).size.width, 700),
                  child: ListView.builder(
                    itemCount: loginState.bases.length,
                    padding: const EdgeInsets.symmetric(horizontal: 18),
                    itemBuilder: (context, index) =>
                        vpnBaseCard(context, index),
                  ),
                ),
              ),
            ),
          ],
        ),
      ),
      floatingActionButton: FloatingActionButton(
        onPressed: () {
          _log.fine("floatingActionButton onPressed()");
          return handleSubmitted();
        },
        tooltip: 'Set up a new base',
        backgroundColor: Theme.of(context).colorScheme.primary,
        child: const Icon(Icons.add),
      ),
    );
  }

  Card vpnBaseCard(BuildContext context, int index) {
    return Card(
        elevation: 7,
        margin: const EdgeInsets.symmetric(vertical: 8, horizontal: 16),
        color: Theme.of(context).colorScheme.secondary,
        child: Column(
          children: [
            InkWell(
                onTap: () {},
                child: Padding(
                  padding: const EdgeInsets.all(8.0),
                  child: ListTile(
                    leading: SvgPicture.asset(
                      'images/server.svg',
                      width: 42,
                    ),
                    title: Text(
                      "VPN base ${loginState.bases[index]['id']}",
                      style: const TextStyle(
                        fontSize: 20,
                        fontWeight: FontWeight.bold,
                        letterSpacing: 1.2,
                      ),
                    ),
                    subtitle: Row(
                      children: [
                        Flexible(
                          flex: 2,
                          child: Text(
                            "19 5 99",
                            style: const TextStyle(
                              fontSize: 13,
                            ),
                            overflow: TextOverflow.visible,
                          ),
                        ),
                        const SizedBox(width: 4),
                        if (true) const Text('|'),
                        const SizedBox(width: 4),
                        if (true)
                          Flexible(
                            child: Text(
                              "231032890",
                              style: const TextStyle(fontSize: 13),
                              overflow: TextOverflow.ellipsis,
                            ),
                          ),
                      ],
                    ),
                    trailing: Icon(
                      Icons.arrow_forward_ios_rounded,
                      color: Theme.of(context).colorScheme.primary,
                    ),
                  ),
                ))
          ],
        ));
  }
}
