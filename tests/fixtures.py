GREENHOUSE = """
<form id="application-form">
  <div class="field"><label for="first_name">First Name <span>*</span></label><input id="first_name" name="job_application[first_name]" type="text" required></div>
  <div class="field"><label for="last_name">Last Name *</label><input id="last_name" name="job_application[last_name]" type="text" required></div>
  <div class="field"><label for="email">Email *</label><input id="email" name="job_application[email]" type="email" required></div>
  <div class="field"><label for="phone">Phone</label><input id="phone" type="tel" name="job_application[phone]"></div>
  <div class="field"><label>Resume/CV *</label><input type="file" id="resume" name="resume" required></div>
  <div class="field"><label>Cover Letter</label><input type="file" id="cover_letter" name="cover_letter"></div>
  <div class="field"><label for="q_linkedin">LinkedIn Profile</label><input id="q_linkedin" name="q1" type="text"></div>
  <div class="field"><label for="q_auth">Are you legally authorized to work in the US? *</label>
    <select id="q_auth" name="q2" required><option value="">Select...</option><option value="1">Yes</option><option value="2">No</option></select></div>
  <div class="field"><label for="q_why">Why do you want to work at Acme? *</label><textarea id="q_why" name="q3" required></textarea></div>
  <div class="field"><label for="gender">Gender</label><select id="gender" name="gender"><option value="">Please select</option><option>Male</option><option>Female</option><option>Decline To Self Identify</option></select></div>
  <input type="hidden" name="token" value="abc">
  <div class="g-recaptcha" data-sitekey="x"></div>
  <textarea name="g-recaptcha-response" style="display:none"></textarea>
  <button type="submit">Submit Application</button>
</form>"""

LEVER = """
<form>
 <ul>
  <li class="application-question"><label><div class="application-label">Full name <span>✱</span></div><div class="application-field"><input type="text" name="name" required></div></label></li>
  <li class="application-question"><label><div class="application-label">Email <span>✱</span></div><div class="application-field"><input type="email" name="email" required></div></label></li>
  <li class="application-question"><label><div class="application-label">Resume/CV ✱</div><div class="application-field"><input type="file" name="resume" required></div></label></li>
  <li class="application-question"><label><div class="application-label">LinkedIn URL</div><div class="application-field"><input type="text" name="urls[LinkedIn]"></div></label></li>
  <li class="application-question"><label><div class="application-label">Will you now or in the future require sponsorship? ✱</div>
     <div class="application-field"><ul>
       <li><label><input type="radio" name="cards[1][field0]" value="Yes" required><span>Yes</span></label></li>
       <li><label><input type="radio" name="cards[1][field0]" value="No"><span>No</span></label></li>
     </ul></div></label></li>
  <li class="application-question"><label><div class="application-label">Additional information</div><textarea name="comments"></textarea></label></li>
 </ul>
 <button type="submit">Submit application</button>
</form>"""
