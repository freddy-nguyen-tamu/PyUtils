chrome.action.onClicked.addListener(async tab => {
  if (!tab?.id || !/^https:\/\/mail\.google\.com\//.test(tab.url||'')) {
    await chrome.tabs.create({url:'https://mail.google.com/'});
    return;
  }
  try {
    await chrome.tabs.sendMessage(tab.id,{type:'GK_TOGGLE'});
  } catch (_) {
    await chrome.scripting.insertCSS({target:{tabId:tab.id},files:['styles.css']});
    await chrome.scripting.executeScript({target:{tabId:tab.id},files:['rules.js','gmail.js','content.js']});
    await chrome.tabs.sendMessage(tab.id,{type:'GK_TOGGLE'});
  }
});
