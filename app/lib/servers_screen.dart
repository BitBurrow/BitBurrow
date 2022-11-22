// ignore_for_file: prefer_const_constructors, prefer_const_literals_to_create_immutables

import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:flutter_svg/flutter_svg.dart';
import 'dart:math';
import 'main.dart';

class ServersScreen extends StatelessWidget {
  const ServersScreen({Key? key}) : super(key: key);

  @override
  Widget build(BuildContext context) {
    const sizedBoxSpace = SizedBox(height: 18);
    return ourScreenLayout(
      context,
      Column(
        children: [
          sizedBoxSpace,
          FractionallySizedBox(
            widthFactor: 0.8,
            child: loginState.servers.isEmpty
                ? Text("You have no VPN servers set up.",
                    textAlign: TextAlign.center,
                    textScaleFactor: 1.8,
                    style: TextStyle(
                      fontStyle: FontStyle.italic,
                      color: Theme.of(context).backgroundColor,
                    ))
                : Text(
                    "Your VPN servers",
                    textAlign: TextAlign.center,
                    textScaleFactor: 1.8,
                    style: TextStyle(fontWeight: FontWeight.bold),
                  ),
          ),
          sizedBoxSpace,
          Expanded(
            child: Center(
              child: SizedBox(
                width: min(MediaQuery.of(context).size.width, 700),
                child: ListView.builder(
                  itemCount: loginState.servers.length,
                  padding: const EdgeInsets.symmetric(horizontal: 18),
                  itemBuilder: (context, index) =>
                      vpnServerCard(context, index),
                ),
              ),
            ),
          ),
        ],
      ),
      floatingActionButton: FloatingActionButton(
        onPressed: () {
          context.push('/new-server');
        },
        tooltip: 'Set up a new server',
        backgroundColor: Theme.of(context).colorScheme.primary,
        child: const Icon(Icons.add),
      ),
    );
  }

  Card vpnServerCard(BuildContext context, int index) {
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
                      "VPN server ${loginState.servers[index]}",
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
