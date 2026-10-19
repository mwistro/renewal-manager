'use strict';
function allowedExternal(value) {
  try {
    const url=new URL(value);
    return url.protocol==='https:' && !url.username && !url.password && (!url.port || url.port==='443') && ['microsoft.com','www.microsoft.com','login.microsoftonline.com','login.microsoft.com','outlook.office.com','outlook.office365.com'].includes(url.hostname);
  } catch {return false;}
}
class NotificationTracker {
  constructor(){this.shown=new Set();}
  fresh(items){return items.filter(item=>!this.shown.has(item.key));}
  mark(items){for(const item of items)this.shown.add(item.key);}
  has(key){return this.shown.has(key);}
}
function closeToTray(event,window,quitting){if(!quitting){event.preventDefault();window.hide();}}
module.exports={allowedExternal,NotificationTracker,closeToTray};
