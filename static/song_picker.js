function initializeSongPicker(picker) {
  const songList = picker.querySelector("[data-song-list]");
  const urlField = picker.querySelector("[data-tab-urls]");
  const search = picker.querySelector("[data-song-search]");
  const results = picker.querySelector("[data-song-search-results]");
  const newSongUrl = picker.querySelector("[data-new-song-url]");
  let searchTimer;

  function syncSongs() {
    const songs = [...songList.querySelectorAll("[data-song-url]")];
    songs.forEach((song, index) => {
      song.querySelector(".editable-song-position").textContent = index + 1;
      song.querySelector("[data-move-up]").disabled = index === 0;
      song.querySelector("[data-move-down]").disabled = index === songs.length - 1;
    });
    urlField.value = songs.map((song) => song.dataset.songUrl).join("\n");
    picker.querySelector("[data-song-count]").textContent = songs.length;
  }

  function action(label, attribute, text) {
    const button = document.createElement("button");
    button.className = attribute === "data-remove-song" ? "danger-action" : "secondary-action";
    button.type = "button";
    button.setAttribute(attribute, "");
    button.setAttribute("aria-label", label);
    button.textContent = text;
    return button;
  }

  function addSong(song) {
    if ([...songList.children].some((row) => row.dataset.songUrl === song.url)) return;
    const row = document.createElement("li");
    row.className = "editable-song";
    row.dataset.songUrl = song.url;
    const position = document.createElement("span");
    position.className = "editable-song-position";
    const details = document.createElement("span");
    details.className = "editable-song-details";
    const title = document.createElement("strong");
    const songTitle = song.title || song.explore_title;
    title.textContent = song.artist && songTitle
      ? `${song.artist} - ${songTitle}`
      : song.artist || songTitle || picker.dataset.newSongLabel;
    const address = document.createElement("span");
    address.textContent = song.url;
    details.append(title, address);
    const actions = document.createElement("span");
    actions.className = "editable-song-actions";
    actions.append(
      action(picker.dataset.moveUpLabel, "data-move-up", "↑"),
      action(picker.dataset.moveDownLabel, "data-move-down", "↓"),
      action(picker.dataset.removeLabel, "data-remove-song", picker.dataset.removeText)
    );
    row.append(position, details, actions);
    songList.append(row);
    syncSongs();
  }

  async function searchSongs() {
    const query = search.value.trim();
    if (query.length < 2) {
      results.hidden = true;
      results.replaceChildren();
      return;
    }
    const searchUrl = new URL(picker.dataset.searchUrl, window.location.origin);
    searchUrl.searchParams.set("q", query);
    const response = await fetch(searchUrl);
    const payload = await response.json();
    results.replaceChildren();
    payload.songs
      .filter((song) => ![...songList.children].some((row) => row.dataset.songUrl === song.url))
      .forEach((song) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "song-search-result";
        button.textContent = [song.artist, song.title || song.explore_title]
          .filter(Boolean)
          .join(" - ") || song.url;
        button.addEventListener("click", () => {
          addSong(song);
          button.remove();
          results.hidden = !results.children.length;
        });
        results.append(button);
      });
    results.hidden = !results.children.length;
  }

  search.addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(searchSongs, 200);
  });

  picker.querySelector("[data-add-song]")?.addEventListener("click", () => {
    const url = newSongUrl.value.trim();
    if (!url || !newSongUrl.checkValidity()) {
      newSongUrl.reportValidity();
      return;
    }
    addSong({ url });
    newSongUrl.value = "";
  });

  songList.addEventListener("click", (event) => {
    const song = event.target.closest("[data-song-url]");
    if (!song) return;
    if (event.target.closest("[data-remove-song]")) song.remove();
    if (event.target.closest("[data-move-up]") && song.previousElementSibling) {
      songList.insertBefore(song, song.previousElementSibling);
    }
    if (event.target.closest("[data-move-down]") && song.nextElementSibling) {
      songList.insertBefore(song.nextElementSibling, song);
    }
    syncSongs();
  });

  syncSongs();
}

document.querySelectorAll("[data-song-picker]").forEach(initializeSongPicker);
