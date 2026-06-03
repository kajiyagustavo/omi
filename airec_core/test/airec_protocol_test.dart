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

    // Testes do bug de realinhamento (devem FALHAR antes da correção)
    test('realinha após frame inválido no meio do stream', () {
      // frameA válido + 10 bytes de lixo sem magic + frameB válido
      // Com o bug atual, B se perde porque o assembler segue fatiando fora de fase.
      final frameA = buildFrame(0xAA);
      final lixo = List.filled(10, 0xFF); // sem magic [P
      final frameB = buildFrame(0xBB);

      final asm = AirecFrameAssembler();
      final packets = asm.addBytes([...frameA, ...lixo, ...frameB]);

      expect(packets.length, 2);
      expect(packets[0].every((b) => b == 0xAA), isTrue,
          reason: 'primeiro pacote deve ser frame A (filler 0xAA)');
      expect(packets[1].every((b) => b == 0xBB), isTrue,
          reason: 'segundo pacote deve ser frame B (filler 0xBB)');
    });

    test('realinha após lixo de tamanho não múltiplo de 82 entre frames', () {
      // frameA + 5 bytes de lixo sem magic + frameC → 2 pacotes na ordem A, C
      final frameA = buildFrame(0xCC);
      final lixo = List.filled(5, 0x01); // 5 bytes, sem magic
      final frameC = buildFrame(0xDD);

      final asm = AirecFrameAssembler();
      final packets = asm.addBytes([...frameA, ...lixo, ...frameC]);

      expect(packets.length, 2);
      expect(packets[0].every((b) => b == 0xCC), isTrue,
          reason: 'primeiro pacote deve ser frame A (filler 0xCC)');
      expect(packets[1].every((b) => b == 0xDD), isTrue,
          reason: 'segundo pacote deve ser frame C (filler 0xDD)');
    });

    // Testes de hardening (comportamento já correto, só fixando em teste)
    test('magic partida em dois chunks de 1 byte', () {
      final asm = AirecFrameAssembler();
      // Primeiro chunk: só o primeiro byte da magic
      final r1 = asm.addBytes([0x5b]);
      expect(r1, isEmpty);
      // Segundo chunk: segundo byte da magic + 80 bytes de payload
      final r2 = asm.addBytes([0x50, ...List.filled(80, 0x42)]);
      expect(r2.length, 1);
      expect(r2[0].length, 80);
      expect(r2[0].every((b) => b == 0x42), isTrue);
    });

    test('muitos chunks sem magic não estouram o buffer', () {
      final asm = AirecFrameAssembler();
      // 50 chamadas de 100 bytes sem magic nenhuma
      for (var i = 0; i < 50; i++) {
        asm.addBytes(List.filled(100, 0x00));
      }
      // Agora entrega um frame válido — deve sair exatamente 1 pacote
      final frame = buildFrame(0x77);
      final packets = asm.addBytes(frame);
      expect(packets.length, 1);
      expect(packets[0].every((b) => b == 0x77), isTrue);
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

    test('parseResponse com body vazio retorna lista vazia (ACK sem corpo)', () {
      // body vazio é válido — representa um ACK sem dados adicionais
      expect(AirecProtocol.parseResponse([0xaa, 0x55]), equals([]));
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
