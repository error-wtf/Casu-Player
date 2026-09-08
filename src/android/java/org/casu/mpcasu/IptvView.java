package org.casu.mpcasu;

import android.app.Activity;
import android.app.AlertDialog;
import android.graphics.Color;
import android.net.Uri;
import android.view.View;
import android.view.ViewGroup;
import android.widget.*;
import android.text.Editable;
import android.text.TextWatcher;
import org.json.JSONArray;
import java.util.*;
import java.util.function.Consumer;

/** Persistent IPTV catalog, independent of the playback queue. */
public final class IptvView extends LinearLayout {
    private final Activity activity;
    private final Consumer<MediaItem> play;
    private final List<MediaItem> channels = new ArrayList<>(), visible = new ArrayList<>();
    private final Set<String> favorites;
    private final android.content.SharedPreferences prefs;
    private final Spinner groups;
    private final EditText search;
    private final ListView list;
    private final TextView status, detail;
    private final ArrayAdapter<String> adapter;
    private String selectedUrl = "";
    private boolean onlyFavorites, loading;
    public IptvView(Activity activity, Consumer<MediaItem> play, Runnable pick) {
        super(activity); this.activity=activity;this.play=play;
        prefs=activity.getSharedPreferences("iptv",0);
        favorites=new HashSet<>(prefs.getStringSet("favorites",Collections.emptySet()));
        setOrientation(VERTICAL);setPadding(dp(12),dp(10),dp(12),dp(76));
        TextView title=label("IPTV · Live TV",22);addView(title);
        LinearLayout tools=new LinearLayout(activity);
        Button file=button("M3U-Datei"),url=button("Playlist-URL"),fav=button("Alle Sender");
        file.setOnClickListener(v->pick.run());
        url.setOnClickListener(v->{ EditText input=new EditText(activity);input.setSingleLine(true);input.setHint("https://…/playlist.m3u");
            input.setText(prefs.getString("source",""));
            new AlertDialog.Builder(activity).setTitle("IPTV-Playlist laden").setView(input)
                .setPositiveButton("Laden",(d,w)->load(input.getText().toString().trim())).setNegativeButton("Abbrechen",null).show(); });
        fav.setOnClickListener(v->{onlyFavorites=!onlyFavorites;fav.setText(onlyFavorites?"★ Favoriten":"Alle Sender");filter();});
        tools.addView(file);tools.addView(url);tools.addView(fav);addView(tools);
        groups=new Spinner(activity);addView(groups);
        search=new EditText(activity);search.setSingleLine(true);search.setTextColor(Color.WHITE);search.setHintTextColor(Color.LTGRAY);search.setHint("Sender oder Gruppe suchen…");addView(search);
        status=label("M3U-Datei oder Playlist-URL laden",14);addView(status);
        list=new ListView(activity);list.setId(View.generateViewId());list.setChoiceMode(ListView.CHOICE_MODE_SINGLE);
        list.setCacheColorHint(Color.TRANSPARENT);list.setSelector(android.R.drawable.list_selector_background);
        adapter=new ArrayAdapter<String>(activity,android.R.layout.simple_list_item_activated_1,new ArrayList<>()) {
            @Override public View getView(int pos,View old,ViewGroup parent) {
                TextView row=(TextView)super.getView(pos,old,parent);row.setTextColor(Color.WHITE);row.setTextSize(18);row.setMinHeight(dp(60));row.setMaxLines(2);return row;
            }
        };
        list.setAdapter(adapter);addView(list,new LinearLayout.LayoutParams(-1,0,1));
        detail=label("Steuerkreuz: auswählen · OK: abspielen",14);addView(detail);
        LinearLayout actions=new LinearLayout(activity);Button watch=button("▶ Abspielen"),star=button("★ Favorit umschalten");
        watch.setOnClickListener(v->activate());star.setOnClickListener(v->{if(selectedUrl.isEmpty())return;if(!favorites.add(selectedUrl))favorites.remove(selectedUrl);prefs.edit().putStringSet("favorites",new HashSet<>(favorites)).apply();filter();});
        actions.addView(watch);actions.addView(star);addView(actions);
        list.setOnItemSelectedListener(new AdapterView.OnItemSelectedListener(){public void onNothingSelected(AdapterView<?> p){} public void onItemSelected(AdapterView<?> p,View v,int pos,long id){select(pos);}});
        list.setOnItemClickListener((p,v,pos,id)->{select(pos);activate();});
        groups.setOnItemSelectedListener(new AdapterView.OnItemSelectedListener(){public void onNothingSelected(AdapterView<?> p){} public void onItemSelected(AdapterView<?> p,View v,int pos,long id){filter();}});
        search.addTextChangedListener(new TextWatcher(){public void beforeTextChanged(CharSequence s,int a,int c,int f){}public void onTextChanged(CharSequence s,int a,int b,int c){filter();}public void afterTextChanged(Editable e){}});
        try {JSONArray saved=new JSONArray(prefs.getString("channels","[]"));for(int i=0;i<saved.length();i++){MediaItem item=MediaItem.fromJson(saved.getJSONObject(i));if(item!=null)channels.add(item);}}catch(Exception ignored){}
        updateGroups();filter();
    }
    private int dp(int n){return Math.round(n*getResources().getDisplayMetrics().density);}
    private TextView label(String text,int size){TextView v=new TextView(activity);v.setText(text);v.setTextSize(size);v.setTextColor(Color.WHITE);v.setPadding(4,4,4,4);return v;}
    private Button button(String text){Button b=new Button(activity);b.setText(text);b.setMinHeight(dp(48));return b;}
    private void select(int pos){if(pos<0||pos>=visible.size())return;MediaItem item=visible.get(pos);selectedUrl=item.url;detail.setText(item.title+" · "+item.playlist);}
    private void activate(){for(MediaItem item:visible)if(item.url.equals(selectedUrl)){play.accept(MediaItem.fromJson(item.toJson()));return;}}
    private void updateGroups(){String selected=groups.getSelectedItem()==null?"Alle Gruppen":groups.getSelectedItem().toString();TreeSet<String> names=new TreeSet<>();for(MediaItem ch:channels)names.add(ch.playlist);List<String> items=new ArrayList<>();items.add("Alle Gruppen");items.addAll(names);groups.setAdapter(new ArrayAdapter<>(activity,android.R.layout.simple_spinner_dropdown_item,items));groups.setSelection(Math.max(0,items.indexOf(selected)));}
    private void filter(){
        if(adapter==null)return;String group=groups.getSelectedItem()==null?"Alle Gruppen":groups.getSelectedItem().toString();String query=search.getText().toString().trim().toLowerCase(Locale.ROOT);
        visible.clear();List<String> labels=new ArrayList<>();int selected=-1;
        for(MediaItem item:channels){if(onlyFavorites&&!favorites.contains(item.url))continue;if(!group.equals("Alle Gruppen")&&!group.equals(item.playlist))continue;if(!(item.title+" "+item.playlist).toLowerCase(Locale.ROOT).contains(query))continue;
            if(item.url.equals(selectedUrl))selected=visible.size();visible.add(item);labels.add((favorites.contains(item.url)?"★ ":"")+item.title+"\n"+item.playlist);}
        adapter.clear();adapter.addAll(labels);if(!visible.isEmpty()){list.setSelection(Math.max(0,selected));list.setItemChecked(Math.max(0,selected),true);}
        status.setText(loading?"Playlist wird geladen…":visible.size()+" / "+channels.size()+" Sender");
        if(visible.isEmpty()){selectedUrl="";detail.setText(channels.isEmpty()?"M3U-Datei oder Playlist-URL laden":"Keine Sender für diesen Filter");}
        else if(selected<0)select(0);
    }
    public void load(String source){if(source.isEmpty()||loading)return;loading=true;filter();
        new Thread(()->{try {
            PlaylistIO.Playlist parsed=PlaylistIO.load(source,p->PlaylistIO.fetchText(activity,p));
            List<MediaItem> loaded=new ArrayList<>();Set<String> seen=new HashSet<>();
            for(PlaylistIO.Entry entry:parsed.items){if(loaded.size()>=10000)throw new IllegalArgumentException("Maximal 10000 Sender");if(!seen.add(entry.url))continue;
                MediaItem item=new MediaItem(entry.url,entry.title,"stream","IPTV");item.playlist=entry.group.isEmpty()?"Ohne Gruppe":entry.group;loaded.add(item);}
            if(loaded.isEmpty())throw new IllegalArgumentException("Keine Sender gefunden");
            JSONArray data=new JSONArray();for(MediaItem item:loaded)data.put(item.toJson());
            prefs.edit().putString("channels",data.toString()).putString("source",source.startsWith("http")?source:"").apply();
            activity.runOnUiThread(()->{loading=false;channels.clear();channels.addAll(loaded);updateGroups();filter();list.requestFocus();});
        }catch(Exception e){activity.runOnUiThread(()->{loading=false;filter();status.setText("Laden fehlgeschlagen: "+e.getMessage());});}},"iptv-import").start();
    }
}
