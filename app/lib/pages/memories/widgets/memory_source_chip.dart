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

/// Human-readable label for a memory's subject (CategoryEnum value), or null
/// when there is nothing worth showing (null/empty or the catch-all "other").
String? memoryTopicLabel(BuildContext context, String? topic) {
  if (topic == null || topic.isEmpty || topic == 'other') return null;
  // Categories arrive lowercase (e.g. "work", "finance"); capitalize first letter,
  // mirroring how conversations render their category tag.
  return topic[0].toUpperCase() + topic.substring(1);
}

/// A small chip showing where a memory came from. Renders nothing
/// (SizedBox.shrink) when the source is null/other/unknown, so callers can
/// include it unconditionally.
Widget buildMemorySourceChip(BuildContext context, String? source) {
  final label = memorySourceLabel(context, source);
  if (label == null) return const SizedBox.shrink();
  return _chip(label: label, icon: memorySourceIcon(source));
}

/// A small chip showing the memory's subject/topic. Renders nothing when the
/// topic is null/empty/other, so callers can include it unconditionally.
Widget buildMemoryTopicChip(BuildContext context, String? topic) {
  final label = memoryTopicLabel(context, topic);
  if (label == null) return const SizedBox.shrink();
  return _chip(label: label, icon: Icons.local_offer_outlined);
}

Widget _chip({required String label, IconData? icon}) {
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
