// Protocolo BLE AIREC — cópia de airec_core/lib/airec_protocol.dart para uso em runtime no app.
// Origem: ~/omi/airec_core/lib/airec_protocol.dart (validado, 21 testes).
// Não editar aqui diretamente — alterações de protocolo devem partir do airec_core.

/// Constantes e extração de pacotes Opus de frames individuais.
class AirecFraming {
  const AirecFraming._();

  static const int frameSize = 82;
  static const int magic0 = 0x5b; // '['
  static const int magic1 = 0x50; // 'P'
  static const int headerBytes = 2;

  /// Retorna os 80 bytes de payload Opus ou null se o frame for inválido.
  ///
  /// Decisão: magic é exatamente 2 bytes — filtrar o 3º byte perderia ~24%
  /// dos frames legítimos (byte 0x48 vs 0x4b é variação normal do aparelho).
  static List<int>? extractOpusPacket(List<int> frame) {
    if (frame.length != frameSize) return null;
    if (frame[0] != magic0 || frame[1] != magic1) return null;
    return frame.sublist(headerBytes);
  }
}

/// Acumula chunks BLE de tamanho arbitrário e emite pacotes Opus
/// ao completar frames de 82 bytes, alinhando-se pela primeira magic.
class AirecFrameAssembler {
  final List<int> _buf = [];
  bool _aligned = false;

  /// Adiciona [data] ao buffer interno e retorna todos os pacotes Opus prontos.
  List<List<int>> addBytes(List<int> data) {
    _buf.addAll(data);

    if (!_aligned) {
      // Procura a primeira ocorrência de magic [0x5b, 0x50] no buffer.
      final idx = _findMagic(_buf);
      if (idx < 0) {
        // Não encontrou; mantém no máximo o último byte como cauda
        // (o '[' pode ter chegado no final do chunk, o 'P' vem no próximo).
        if (_buf.isNotEmpty) {
          final last = _buf.last;
          _buf.clear();
          if (last == AirecFraming.magic0) _buf.add(last);
        }
        return const [];
      }
      // Descarta o lixo antes da magic.
      _buf.removeRange(0, idx);
      _aligned = true;
    }

    final packets = <List<int>>[];
    // TODO(perf): buffer O(n) em removeRange; trocar por Uint8List+índice se a frequência de chunks crescer.
    while (_buf.length >= AirecFraming.frameSize) {
      final frame = _buf.sublist(0, AirecFraming.frameSize);
      final pkt = AirecFraming.extractOpusPacket(frame);
      if (pkt != null) {
        _buf.removeRange(0, AirecFraming.frameSize);
        packets.add(pkt);
      } else {
        // Frame inválido após alinhamento: stream perdeu sincronismo (glitch/reconexão).
        // Descarta 1 byte (evita reencontrar a mesma posição) e volta a buscar a magic.
        _buf.removeRange(0, 1);
        _aligned = false;
        break;
      }
    }

    // Se perdemos o alinhamento dentro do loop acima, tenta realinhar agora
    // com o que sobrou no buffer — pode já conter a próxima magic.
    if (!_aligned && _buf.isNotEmpty) {
      final idx = _findMagic(_buf);
      if (idx < 0) {
        if (_buf.isNotEmpty) {
          final last = _buf.last;
          _buf.clear();
          if (last == AirecFraming.magic0) _buf.add(last);
        }
        return packets;
      }
      _buf.removeRange(0, idx);
      _aligned = true;
      // Tenta extrair frames completos do buffer reposicionado.
      while (_buf.length >= AirecFraming.frameSize) {
        final frame = _buf.sublist(0, AirecFraming.frameSize);
        final pkt = AirecFraming.extractOpusPacket(frame);
        if (pkt != null) {
          _buf.removeRange(0, AirecFraming.frameSize);
          packets.add(pkt);
        } else {
          _buf.removeRange(0, 1);
          _aligned = false;
          break;
        }
      }
    }

    return packets;
  }

  /// Descarta estado entre conexões — evita que buffer residual embaralhe o framing numa reconexão.
  void reset() {
    _buf.clear();
    _aligned = false;
  }

  static int _findMagic(List<int> buf) {
    for (int i = 0; i < buf.length - 1; i++) {
      if (buf[i] == AirecFraming.magic0 && buf[i + 1] == AirecFraming.magic1) {
        return i;
      }
    }
    return -1;
  }
}

/// Comandos e respostas do protocolo AIREC.
class AirecProtocol {
  const AirecProtocol._();

  static const List<int> cmdMagic = [0x55, 0xaa];
  static const List<int> respMagic = [0xaa, 0x55];

  static List<int> encodeCommand(List<int> body) => [...cmdMagic, ...body];

  static List<int> get startStream => encodeCommand([0x01, 0x03]);
  static List<int> get stopStream => encodeCommand([0x01, 0x04]);

  /// Comando de data-hora: 55aa0f02 + 14 chars ASCII do timestamp (AAAAMMDDHHMMSS).
  /// A Fase 1 (Python) provou que SEM este comando o device não arma o stream de áudio.
  static List<int> cmdDatahora(DateTime now) {
    final ts = '${now.year.toString().padLeft(4, '0')}'
        '${now.month.toString().padLeft(2, '0')}'
        '${now.day.toString().padLeft(2, '0')}'
        '${now.hour.toString().padLeft(2, '0')}'
        '${now.minute.toString().padLeft(2, '0')}'
        '${now.second.toString().padLeft(2, '0')}';
    return [0x55, 0xaa, 0x0f, 0x02, ...ts.codeUnits];
  }

  /// Retorna null se [data] não começa com respMagic ou tem menos de 2 bytes.
  /// Body vazio (apenas os 2 bytes de magic) é válido — representa um ACK sem corpo.
  static List<int>? parseResponse(List<int> data) {
    if (data.length < 2) return null;
    if (data[0] != respMagic[0] || data[1] != respMagic[1]) return null;
    return data.sublist(2);
  }

  /// Sequência de handshake validada fisicamente com o aparelho AIREC (Fase 1, Python).
  /// Ordem importa — não reordenar. O comando de data-hora (cmdDatahora) entra entre
  /// 55aa026600 e 55aa0130, exatamente como em montar_handshake() do protocolo.py.
  /// SEM ele o device responde os comandos mas NÃO inicia o stream de áudio.
  static List<List<int>> handshakeSeq(DateTime now) => [
        [0x55, 0xaa, 0x01, 0x01],
        [0x55, 0xaa, 0x01, 0x20],
        [0x55, 0xaa, 0x01, 0x0f],
        [0x55, 0xaa, 0x01, 0x0e],
        [0x55, 0xaa, 0x01, 0x36],
        [0x55, 0xaa, 0x01, 0x0b],
        [0x55, 0xaa, 0x01, 0x26],
        [0x55, 0xaa, 0x02, 0x69, 0x00],
        [0x55, 0xaa, 0x01, 0x05],
        [0x55, 0xaa, 0x02, 0x66, 0x00],
        cmdDatahora(now), // <-- comando de data-hora (faltava)
        [0x55, 0xaa, 0x01, 0x30],
        [0x55, 0xaa, 0x01, 0x05],
        [0x55, 0xaa, 0x01, 0x0e],
        [0x55, 0xaa, 0x01, 0x36],
        [0x55, 0xaa, 0x02, 0x38, 0x01],
        [0x55, 0xaa, 0x02, 0x38, 0x00],
        [0x55, 0xaa, 0x02, 0x3a, 0x01],
        [0x55, 0xaa, 0x01, 0x03],
        [0x55, 0xaa, 0x02, 0x21, 0x54],
      ];

  /// Mantido por compatibilidade (sequência sem data-hora — NÃO inicia áudio).
  @Deprecated('Use handshakeSeq(now) — inclui o comando de data-hora obrigatório')
  static List<List<int>> get handshake => handshakeSeq(DateTime(2026, 1, 1));
}
