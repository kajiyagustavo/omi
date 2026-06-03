import 'dart:async';

import 'package:omi/backend/schema/bt_device/bt_device.dart';
import 'package:omi/services/devices/device_connection.dart';
import 'package:omi/services/devices/models.dart';
import 'package:omi/services/devices/airec_protocol.dart';
import 'package:omi/utils/logger.dart';

class AirecDeviceConnection extends DeviceConnection {
  final StreamController<List<int>> _audioStream = StreamController<List<int>>.broadcast();
  final AirecFrameAssembler _assembler = AirecFrameAssembler();
  StreamSubscription? _responseSub;
  StreamSubscription? _audioSub;
  bool _streaming = false;

  AirecDeviceConnection(super.device, super.transport);

  @override
  Future<void> connect({
    void Function(String deviceId, DeviceConnectionState state)? onConnectionStateChanged,
  }) async {
    await super.connect(onConnectionStateChanged: onConnectionStateChanged);
    _assembler.reset();
    await Future.delayed(const Duration(seconds: 1));

    // Inscreve response char primeiro para capturar ACKs do handshake.
    _responseSub = transport
        .getCharacteristicStream(airecServiceUuid, airecResponseCharUuid)
        .listen((data) {
      final body = AirecProtocol.parseResponse(data);
      if (body == null) Logger.debug('[AIREC] resposta inesperada: $data');
    });

    _audioSub = transport
        .getCharacteristicStream(airecServiceUuid, airecAudioCharUuid)
        .listen((data) {
      for (final opus in _assembler.addBytes(data)) {
        _audioStream.add(opus);
      }
    });
  }

  @override
  Future<void> disconnect() async {
    if (_streaming) {
      try {
        await transport.writeCharacteristic(
          airecServiceUuid,
          airecCommandCharUuid,
          AirecProtocol.stopStream,
        );
      } catch (_) {}
      _streaming = false;
    }
    await _responseSub?.cancel();
    await _audioSub?.cancel();
    if (!_audioStream.isClosed) {
      await _audioStream.close();
    }
    await super.disconnect();
  }

  Future<void> _runHandshake() async {
    for (final cmd in AirecProtocol.handshake) {
      await transport.writeCharacteristic(airecServiceUuid, airecCommandCharUuid, cmd);
      await Future.delayed(const Duration(milliseconds: 120));
    }
    _streaming = true;
  }

  // --- Métodos abstratos obrigatórios ---

  @override
  Future<int> performRetrieveBatteryLevel() async => -1;

  @override
  Future<StreamSubscription<List<int>>?> performGetBleBatteryLevelListener({
    void Function(int)? onBatteryLevelChange,
  }) async =>
      null;

  @override
  Future<List<int>> performGetButtonState() async => [];

  @override
  Future<BleAudioCodec> performGetAudioCodec() async => BleAudioCodec.opus;

  @override
  Future<StreamSubscription?> performGetBleAudioBytesListener({
    required void Function(List<int>) onAudioBytesReceived,
  }) async {
    await _runHandshake();
    return _audioStream.stream.listen(onAudioBytesReceived);
  }

  @override
  Future<StreamSubscription?> performGetBleStorageBytesListener({
    required void Function(List<int>) onStorageBytesReceived,
  }) async =>
      null;

  @override
  Future performCameraStartPhotoController() async {}

  @override
  Future performCameraStopPhotoController() async {}

  @override
  Future<bool> performHasPhotoStreamingCharacteristic() async => false;

  @override
  Future<StreamSubscription?> performGetImageListener({
    required void Function(OrientedImage orientedImage) onImageReceived,
  }) async =>
      null;

  @override
  Future<StreamSubscription<List<int>>?> performGetAccelListener({
    void Function(int)? onAccelChange,
  }) async =>
      null;

  @override
  Future<int> performGetFeatures() async => 0;

  @override
  Future<void> performSetLedDimRatio(int ratio) async {}

  @override
  Future<int?> performGetLedDimRatio() async => null;

  @override
  Future<void> performSetMicGain(int gain) async {}

  @override
  Future<int?> performGetMicGain() async => null;
}
