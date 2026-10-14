CourseSite.start().catch(error => {
  document.getElementById("entries").textContent = error.message;
});
