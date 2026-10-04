const speed = document.getElementById("speed");
const value = document.getElementById("value");

chrome.storage.local.get({ autoScrollSpeed: 70 }, ({ autoScrollSpeed }) => {
  speed.value = autoScrollSpeed;
  value.textContent = `${autoScrollSpeed} px/s`;
});

speed.addEventListener("input", () => {
  const n = Number(speed.value);
  value.textContent = `${n} px/s`;
  chrome.storage.local.set({ autoScrollSpeed: n });
});