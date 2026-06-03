import 'package:test/test.dart';
import 'package:airec_core/airec_protocol.dart';

void main() {
  // ──────────────────────────────────────────────
  // AirecFraming
  // ──────────────────────────────────────────────
  group('AirecFraming', () {
    List<int> frameWith(int filler) =>
        [AirecFraming.magic0, AirecFraming.magic1, ...List.filled(80, filler)];

    test('frame válido [P retorna 80 bytes de payload', () {
      final packet = AirecFraming.extractOpusPacket(frameWith(0x11));
      expect(packet, isNotNull);
      expect(packet!.length, 80);
      expect(packet.every((b) => b == 0x11), isTrue);
    });

    test('frame [PH (3º byte = 0x48) também retorna 80 bytes, 1º byte do payload = 0x48', () {
      // magic só são os 2 primeiros bytes; 0x48 no byte 2 é payload legítimo
      final frame = [0x5b, 0x50, 0x48, ...List.filled(79, 0x00)];
      final packet = AirecFraming.extractOpusPacket(frame);
      expect(packet, isNotNull);
      expect(packet!.length, 80);
      expect(packet.first, 0x48);
    });

    test('frame sem magic retorna null', () {
      final frame = [0x00, 0x01, ...List.filled(80, 0x00)];
      expect(AirecFraming.extractOpusPacket(frame), isNull);
    });

    test('frame com tamanho errado retorna null', () {
      expect(AirecFraming.extractOpusPacket([0x5b, 0x50, 0x01]), isNull);
      expect(AirecFraming.extractOpusPacket([]), isNull);
    });
  });

  // ──────────────────────────────────────────────
  // AirecFrameAssembler
  // ──────────────────────────────────────────────
  group('AirecFrameAssembler', () {
    List<int> buildFrame(int filler) =>
        [0x5b, 0x50, ...List.filled(80, filler)];

    test('dois frames entregues em 3 chunks irregulares emitem 2 pacotes na ordem certa', () {
      final frameA = buildFrame(0x11);
      final frameB = buildFrame(0x22);
      final stream = [...frameA, ...frameB]; // 164 bytes

      // divide em 3 chunks: [0..50], [51..120], [121..163]
      final c1 = stream.sublist(0, 51);
      final c2 = stream.sublist(51, 121);
      final c3 = stream.sublist(121);

      final asm = AirecFrameAssembler();
      final out1 = asm.addBytes(c1);
      final out2 = asm.addBytes(c2);
      final out3 = asm.addBytes(c3);

      final all = [...out1, ...out2, ...out3];
      expect(all.length, 2);
      expect(all[0].length, 80);
      expect(all[0].every((b) => b == 0x11), isTrue);
      expect(all[1].length, 80);
      expect(all[1].every((b) => b == 0x22), isTrue);
    });

    test('lixo antes da magic é descartado e frame único alinhado emite 1 pacote', () {
      final lixo = [0xAA, 0xBB];
      final frame = buildFrame(0x33);
      final asm = AirecFrameAssembler();
      final packets = asm.addBytes([...lixo, ...frame]);
      expect(packets.length, 1);
      expect(packets[0].length, 80);
      expect(packets[0].every((b) => b == 0x33), isTrue);
    });
  });

  // ──────────────────────────────────────────────
  // AirecProtocol
  // ──────────────────────────────────────────────
  group('AirecProtocol', () {
    test('encodeCommand prefixo correto', () {
      expect(
        AirecProtocol.encodeCommand([0x01, 0x03]),
        [0x55, 0xaa, 0x01, 0x03],
      );
    });

    test('startStream == [0x55,0xaa,0x01,0x03]', () {
      expect(AirecProtocol.startStream, [0x55, 0xaa, 0x01, 0x03]);
    });

    test('stopStream == [0x55,0xaa,0x01,0x04]', () {
      expect(AirecProtocol.stopStream, [0x55, 0xaa, 0x01, 0x04]);
    });

    test('parseResponse retorna body quando prefixo correto', () {
      expect(
        AirecProtocol.parseResponse([0xaa, 0x55, 0x01, 0x03]),
        [0x01, 0x03],
      );
    });

    test('parseResponse retorna null com prefixo errado', () {
      expect(AirecProtocol.parseResponse([0x00, 0x01]), isNull);
    });

    test('parseResponse retorna null com menos de 2 bytes', () {
      expect(AirecProtocol.parseResponse([0xaa]), isNull);
      expect(AirecProtocol.parseResponse([]), isNull);
    });

    test('handshake tem exatamente 19 sequências', () {
      expect(AirecProtocol.handshake.length, 19);
    });

    test('handshake.last == [0x55,0xaa,0x02,0x21,0x54]', () {
      expect(AirecProtocol.handshake.last, [0x55, 0xaa, 0x02, 0x21, 0x54]);
    });

    test('todos os comandos do handshake começam com [0x55,0xaa]', () {
      for (final cmd in AirecProtocol.handshake) {
        expect(cmd.take(2).toList(), [0x55, 0xaa]);
      }
    });
  });
}
