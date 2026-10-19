'use strict';
const {contextBridge,ipcRenderer}=require('electron');
contextBridge.exposeInMainWorld('renovacionesDesktop',Object.freeze({
  configure:mailbox=>ipcRenderer.invoke('desktop:configure',mailbox),
  settings:()=>ipcRenderer.invoke('desktop:settings'),
  notifications:enabled=>ipcRenderer.invoke('desktop:notifications',enabled===true),
  wake:()=>ipcRenderer.invoke('desktop:wake')
}));
window.addEventListener('online',()=>ipcRenderer.invoke('desktop:wake').catch(()=>{}));
