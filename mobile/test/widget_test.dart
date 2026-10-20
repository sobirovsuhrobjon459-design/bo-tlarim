import 'package:flutter_test/flutter_test.dart';
import 'package:chittak/main.dart';

void main() {
  test('ilova chittak.uz saytini ochadi', () {
    expect(appUrl, 'https://www.chittak.uz/');
    expect(appHost, 'www.chittak.uz');
    expect(ownHosts, containsAll(['www.chittak.uz', 'chittak.uz']));
  });
}
