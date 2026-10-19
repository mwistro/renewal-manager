'use strict';
// El adaptador se inyecta para probar carreras y fallos sin descargar ni instalar.
class Updates {
  constructor({updater,dialog,notify,stopEngine,resumeEngine,changed,setInstalling,version}) {
    Object.assign(this,{updater,dialog,notify,stopEngine,resumeEngine,changed,setInstalling});
    this.status='Buscar actualizaciones';this.ready=false;this.busy=false;this.prompting=false;this.installing=false;
    updater.autoDownload=true;
    updater.autoInstallOnAppQuit=false; // «Más tarde» y «Salir» nunca instalan a escondidas.
    updater.allowPrerelease=version.includes('-beta.');
    updater.channel=updater.allowPrerelease?'beta':'latest';
    updater.allowDowngrade=false;
    updater.disableWebInstaller=true;
    updater.logger=null;
    updater.on('checking-for-update',()=>this.label('Buscando actualizaciones…'));
    updater.on('update-available',()=>this.label('Descargando actualización…'));
    updater.on('download-progress',progress=>this.label('Descargando actualización: '+Math.floor(progress.percent)+' %'));
    updater.on('update-not-available',()=>this.label('Aplicación actualizada'));
    updater.on('error',()=>{
      this.busy=false;
      this.label('No se pudo actualizar · Reintentar');
      if(this.installing){this.installing=false;this.setInstalling(false);void this.resumeEngine();}
    });
    updater.on('update-downloaded',info=>{
      this.ready=true;this.busy=false;this.version=info.version;this.label('Reiniciar y actualizar');
      this.notify('Actualización preparada','La versión '+info.version+' está lista. Puedes instalarla cuando termines.',()=>void this.offer());
      void this.offer();
    });
  }
  label(value){this.status=value;this.changed();}
  start(){void this.check();this.timer=setInterval(()=>void this.check(),5*60*1000);}
  dispose(){clearInterval(this.timer);}
  async check(manual=false){
    if(this.ready){if(manual)await this.offer();return;}
    if(this.busy||this.installing)return;
    this.busy=true;
    try{const result=await this.updater.checkForUpdates();if(result?.downloadPromise)await result.downloadPromise;
      if(manual&&!this.ready&&this.status==='Aplicación actualizada')await this.dialog.showMessageBox({type:'info',title:'Actualizaciones',message:'Ya tienes la última versión publicada.',buttons:['Aceptar']});
    }catch{this.label('No se pudo actualizar · Reintentar');if(manual)await this.dialog.showMessageBox({type:'info',title:'Actualizaciones',message:'No se ha podido comprobar o descargar la actualización. La versión actual sigue funcionando.',buttons:['Aceptar']});}
    finally{this.busy=false;}
  }
  async offer(){
    if(!this.ready||this.prompting||this.installing)return;
    this.prompting=true;
    try{
      const choice=await this.dialog.showMessageBox({type:'info',title:'Actualización preparada',message:'La versión '+this.version+' está lista.',detail:'Se conservarán tu configuración, sesión y renovaciones. Si hay una operación en curso, se esperará a que termine.',buttons:['Reiniciar y actualizar','Más tarde'],defaultId:1,cancelId:1,noLink:true});
      if(choice.response===0)await this.install();
    }finally{this.prompting=false;}
  }
  async install(){
    if(!this.ready||this.installing)return;
    this.installing=true;this.label('Esperando a que termine la operación…');
    try{
      await this.stopEngine(); // Barrera confirmada y proceso terminado, sin kill.
      this.setInstalling(true);
      this.updater.quitAndInstall(false,true);
    }catch{
      this.installing=false;this.setInstalling(false);this.label('Actualización aplazada · Reintentar');
      await this.resumeEngine();
      await this.dialog.showMessageBox({type:'info',title:'Actualización aplazada',message:'No se ha podido cerrar el servicio de forma segura. La actualización no se ha iniciado.',buttons:['Aceptar']});
    }
  }
}
module.exports={Updates};
