import 'package:flutter/material.dart';

const Map<String, String> _sourceLabels = {
  'whatsapp': 'WhatsApp',
  'recording': 'Recording',
  'plaud': 'Plaud',
  'email': 'Email',
  'manual': 'Manual',
};

const Map<String, IconData> _sourceIcons = {
  'whatsapp': Icons.chat_bubble_outline,
  'recording': Icons.mic_none,
  'plaud': Icons.memory,
  'email': Icons.mail_outline,
  'manual': Icons.edit_outlined,
};

/// Human-readable label for a memory's provenance, or null for unknown/other.
String? memorySourceLabel(BuildContext context, String? source) {
  if (source == null) return null;
  return _sourceLabels[source];
}

/// Icon for a memory's provenance, or null for unknown/other.
IconData? memorySourceIcon(String? source) {
  if (source == null) return null;
  return _sourceIcons[source];
}

/// A small chip showing where a memory came from. Renders nothing
/// (SizedBox.shrink) when the source is null/other/unknown, so callers can
/// include it unconditionally.
Widget buildMemorySourceChip(BuildContext context, String? source) {
  final label = memorySourceLabel(context, source);
  if (label == null) return const SizedBox.shrink();
  final icon = memorySourceIcon(source);
  return Container(
    padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
    decoration: BoxDecoration(
      color: Colors.white.withValues(alpha: 0.08),
      borderRadius: BorderRadius.circular(8),
    ),
    child: Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        if (icon != null) ...[
          Icon(icon, size: 12, color: Colors.white70),
          const SizedBox(width: 4),
        ],
        Text(label, style: const TextStyle(fontSize: 11, color: Colors.white70)),
      ],
    ),
  );
}
