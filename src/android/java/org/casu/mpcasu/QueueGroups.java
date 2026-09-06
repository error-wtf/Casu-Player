package org.casu.mpcasu;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.Set;

/** Presentation rows retain the engine's per-video playback order. */
public final class QueueGroups {
    public static final class Row {
        public final int index;
        public final int end;
        public final boolean header;
        Row(int index, int end, boolean header) { this.index = index; this.end = end; this.header = header; }
    }
    public static List<Row> rows(List<MediaItem> items, Set<String> expanded, String query) {
        List<Row> out = new ArrayList<>();
        query = query.toLowerCase(Locale.ROOT);
        for (int start = 0; start < items.size();) {
            MediaItem item = items.get(start);
            String group = item.playlist;
            int end = start + 1;
            if (group != null && !group.isEmpty()) {
                while (end < items.size() && group.equals(items.get(end).playlist)) end++;
                List<Row> children = new ArrayList<>();
                for (int i = start; i < end; i++) if (matches(items.get(i), query)) children.add(new Row(i, i + 1, false));
                if (!children.isEmpty()) {
                    out.add(new Row(start, end, true));
                    if (!query.isEmpty() || expanded.contains(group)) out.addAll(children);
                }
            } else if (matches(item, query)) out.add(new Row(start, end, false));
            start = end;
        }
        return out;
    }
    private static boolean matches(MediaItem item, String query) {
        return query.isEmpty() || (item.title + " " + item.url + " " + item.badge + " " + item.playlist)
            .toLowerCase(Locale.ROOT).contains(query);
    }
}
