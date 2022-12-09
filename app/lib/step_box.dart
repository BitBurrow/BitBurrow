import 'package:flutter/material.dart';
import 'package:logging/logging.dart';
import 'main.dart';

final _log = Logger('step_box');

enum StepTypes {
  checkbox, // user can check, uncheck
  process, // automated, has cancel button, can be retried
  button, // e.g. "CONFIGURE ROUTER"
}

class StepData {
  StepData({
    required this.text,
    required this.type,
  });
  String text;
  StepTypes type;
}

class StepBox extends StatelessWidget {
  const StepBox({
    super.key,
    this.onCheckboxTap,
    this.onButtonPress,
    required this.data,
    required this.isChecked, // or 'pressed' for buttons
    required this.isActive, // last checked step or first unchecked step
    required this.isLastStep,
  });

  final void Function(bool?)? onCheckboxTap;
  final void Function()? onButtonPress;
  final StepData data;
  final bool isChecked;
  final bool isActive;
  final bool isLastStep;

  @override
  Widget build(context) {
    bool isCheckbox = data.type == StepTypes.checkbox;
    bool isProcess = data.type == StepTypes.process;
    bool isButton = data.type == StepTypes.button;
    bool isNextStep = isActive && !isChecked;
    return isButton
        // StepTypes.button
        ? Column(
            children: !isLastStep
                ? [] // hide button when it's not the last step
                : [
                    const SizedBox(height: 24),
                    Center(
                      child: ElevatedButton(
                        onPressed: isNextStep
                            ? onButtonPress
                            : null, // disabled until all steps are done
                        child: Text(data.text.trim()),
                      ),
                    ),
                  ],
          )
        // StepTypes.checkbox OR StepTypes.process
        : Row(
            crossAxisAlignment: CrossAxisAlignment.start, // top-align
            children: [
              // checkbox
              SizedBox(
                width: 52, // 3 + 26 + 23
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start, // top-align
                  children: [
                    // SizedBox() sizes below mimic CheckboxListTile() with:
                    //   controlAffinity: ListTileControlAffinity.leading,
                    //   contentPadding: EdgeInsets.zero, dense: true,
                    const SizedBox(height: 52, width: 3),
                    SizedBox(
                      height: 26,
                      width: 26,
                      child: isCheckbox
                          ? Checkbox(
                              value: isChecked,
                              onChanged: onCheckboxTap,
                            )
                          : (isProcess && !isNextStep)
                              ? Checkbox(
                                  value: isChecked ? true : null,
                                  tristate: true,
                                  onChanged: null,
                                )
                              : Transform.scale(
                                  scale: 1.4,
                                  child: const CircularProgressIndicator(
                                    strokeWidth: 4,
                                  ),
                                ),
                    ),
                  ],
                ),
              ),
              // title and text
              Expanded(
                  child: Column(
                crossAxisAlignment: CrossAxisAlignment.start, // left-align text
                children: [
                  textMd(context, data.text),
                  if (isNextStep && isProcess)
                    Row(
                      mainAxisAlignment:
                          MainAxisAlignment.end, // right-align button
                      children: [
                        TextButton(
                            onPressed: () {
                              _log.fine("TextButton 'CANCEL' onPressed()");
                            },
                            child: const Text("CANCEL"))
                      ],
                    ),
                  const SizedBox(height: 16), // spacing between steps
                ],
              )),
            ],
          );
  }
}
