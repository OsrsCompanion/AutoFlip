
// PATCH: fix role detection (guest/mod)
function getRoleBadge(user){
  if(!user || !user.email) return {label:"GUEST", class:"guest"};

  if(user.email.includes(".mod")){
    return {label:"MODERATOR", class:"mod"};
  }
  return {label:"USER", class:"user"};
}

function applyProfileHeader(user){
  const badge = getRoleBadge(user);
  const badgeEl = document.querySelector(".profile-badge");
  if(badgeEl){
    badgeEl.textContent = badge.label;
    badgeEl.className = "profile-badge " + badge.class;
  }
}
