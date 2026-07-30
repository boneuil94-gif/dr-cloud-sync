(() => {
  const form=document.querySelector('#passwordForm'), message=document.querySelector('#passwordMessage');
  form.addEventListener('submit',async event=>{
    event.preventDefault(); message.hidden=true;
    const data=Object.fromEntries(new FormData(form));
    try {
      const response=await fetch('/api/security/change-password',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':document.querySelector('meta[name=csrf-token]').content},body:JSON.stringify(data)});
      const value=await response.json();
      if(!response.ok) throw new Error(value.error||'Changement impossible.');
      form.reset(); window.location.assign('/login');
    } catch(error) { form.reset(); message.textContent=error.message; message.hidden=false; }
  });
})();
