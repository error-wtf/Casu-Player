package org.casu.mpcasu;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import org.junit.Test;
import org.junit.runner.RunWith;
import java.util.*;
import static org.junit.Assert.*;

@RunWith(AndroidJUnit4.class)
public class PlaylistGroupsInstrumentedTest {
    @Test public void groupsRemainSeparateAndExpandWithoutChangingPlaybackOrder() throws Exception {
        MediaItem a = new MediaItem("https://a", "Alpha", "youtube", "YT"); a.playlist = "Playlist A";
        MediaItem b = new MediaItem("https://b", "Beta", "youtube", "YT"); b.playlist = "Playlist A";
        MediaItem c = new MediaItem("https://c", "Gamma", "youtube", "YT"); c.playlist = "Playlist B";
        List<MediaItem> items = Arrays.asList(a,b,c);
        List<QueueGroups.Row> collapsed = QueueGroups.rows(items, Collections.emptySet(), "");
        assertEquals(2, collapsed.size()); assertTrue(collapsed.get(0).header);
        assertEquals(2, collapsed.get(0).end);
        List<QueueGroups.Row> expanded = QueueGroups.rows(items, Collections.singleton("Playlist A"), "");
        assertEquals(4, expanded.size()); assertFalse(expanded.get(1).header);
        assertEquals(0, expanded.get(1).index); assertEquals(1, expanded.get(2).index);
        List<QueueGroups.Row> filtered = QueueGroups.rows(items, Collections.emptySet(), "Beta");
        assertEquals(2, filtered.size()); assertEquals(1, filtered.get(1).index);
        assertEquals("Playlist A", MediaItem.fromJson(a.toJson()).playlist);
    }
    @Test public void exportsUsePermanentYoutubeLinksAndUnicodeTitles() throws Exception {
        MediaItem item = new MediaItem("https://temporary.googlevideo.com/expired", "Überall", "youtube", "YT");
        item.sourceUrl = "https://www.youtube.com/watch?v=abcdefghijk";
        List<MediaItem> items = Collections.singletonList(item);
        for (String output : Arrays.asList(PlaylistIO.writeM3u("Mix", items), PlaylistIO.writePls(items), PlaylistIO.writeXspf("Mix", items), PlaylistIO.writeJspf("Mix", items))) {
            assertTrue(output.contains("abcdefghijk")); assertFalse(output.contains("expired")); assertTrue(output.contains("Überall"));
        }
        assertEquals(item.sourceUrl, MediaItem.fromJson(item.toJson()).sourceUrl);
    }
}
