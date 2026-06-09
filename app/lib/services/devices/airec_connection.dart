import 'dart:async';

import 'package:omi/backend/schema/bt_device/bt_device.dart';
import 'package:omi/services/devices.dart';
import 'package:omi/services/devices/device_connection.dart';
import 'package:omi/services/devices/models.dart';
import 'package:omi/services/devices/airec_protocol.dart';
import 'package:omi/services/devices/transports/device_transport.dart';
import 'package:omi/utils/logger.dart';

class AirecDeviceConnection extends DeviceConnection {
  final StreamController<List<int>> _audioStream = StreamController<List<int>>.broadcast();
  final AirecFrameAssembler _assembler = AirecFrameAssembler();
  StreamSubscription? _responseSub;
  StreamSubscription? _audioSub;
  StreamSubscription? _statusSub;
  StreamSubscription? _connStateSub;
  bool _streaming = false;
  // True após o primeiro handshake bem-sucedido. Distingue uma RECONEXÃO (onde
  // precisamos refazer o handshake) da conexão inicial (onde performGetBleAudioBytesListener
  // já cuida do handshake). Sem isso, o device reconecta mas nunca volta a transmitir áudio.
  bool _wasStreaming = false;

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

    // Inscreve a char de STATUS (0x000b) como a Fase 1 (Python) faz — sem isso o
    // device pode não armar o stream. Não processamos o conteúdo, só mantemos ativa.
    _statusSub = transport.getCharacteristicStream(airecServiceUuid, airecStatusCharUuid).listen((_) {});

    // Reconexão: o transporte nativo re-subscreve as características sozinho, mas o
    // device NÃO volta a transmitir áudio até receber o handshake de novo. Sem isto,
    // após cair e reconectar, o app mostra "Ouvindo" mas chegam 0 bytes (bug do repareamento).
    _connStateSub = transport.connectionStateStream.listen((state) async {
      if (state == DeviceTransportState.disconnected) {
        // Caiu: o stream do device morreu. Marca para refazer o handshake ao voltar.
        _streaming = false;
      } else if (state == DeviceTransportState.connected && _wasStreaming && !_streaming) {
        // Dá tempo do re-subscribe nativo assentar antes de mandar o device transmitir.
        await Future.delayed(const Duration(milliseconds: 500));
        if (_wasStreaming && !_streaming) {
          Logger.debug('[AIREC] reconexão detectada — refazendo handshake');
          await _runHandshake();
        }
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
    _wasStreaming = false;
    await _connStateSub?.cancel();
    await _responseSub?.cancel();
    await _audioSub?.cancel();
    await _statusSub?.cancel();
    if (!_audioStream.isClosed) {
      await _audioStream.close();
    }
    await super.disconnect();
  }

  Future<void> _runHandshake() async {
    // Sequência com o comando de data-hora real (timestamp). Sem ele o device
    // responde os comandos mas não inicia o stream de áudio (validado Fase 1).
    for (final cmd in AirecProtocol.handshakeSeq(DateTime.now())) {
      await transport.writeCharacteristic(airecServiceUuid, airecCommandCharUuid, cmd);
      await Future.delayed(const Duration(milliseconds: 250));
    }
    _streaming = true;
    _wasStreaming = true;
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
  // opusFS320: o frame Opus do AIREC tem 80B de payload mas decodifica para 320 samples
  // (20ms @16kHz). Com 'opus' o backend usa frame_size=160 e dá "buffer too small".
  // opusFS320 faz o backend decodar com frame_size=320 (correto p/ os frames do AIREC).
  Future<BleAudioCodec> performGetAudioCodec() async => BleAudioCodec.opusFS320;

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
