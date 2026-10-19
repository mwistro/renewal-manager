document.getElementById('setup').addEventListener('submit',async event=>{
  event.preventDefault();const button=document.getElementById('continue');button.disabled=true;
  try{await window.renovacionesDesktop.configure(document.getElementById('mailbox').value.trim());}
  catch{document.getElementById('error').textContent='No se ha podido guardar la cuenta. Comprueba la dirección e inténtalo de nuevo.';button.disabled=false;}
});
