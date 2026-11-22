import { Application, Controller } from '/static/vendor/stimulus/stimulus.js';

class ToastController extends Controller {
  connect() {
    this.element.dataset.toastReady = 'true';
    this.onNotice = event => this.show(event.detail);
    this.element.addEventListener('inboxtend:notice', this.onNotice);
  }

  disconnect() {
    clearTimeout(this.dismissTimer);
    this.element.removeEventListener('inboxtend:notice', this.onNotice);
    delete this.element.dataset.toastReady;
  }

  show({message, error = false}) {
    clearTimeout(this.dismissTimer);
    this.element.textContent = message;
    this.element.classList.toggle('error', error);
    if (message) {
      this.dismissTimer = setTimeout(() => { this.element.textContent = ''; }, error ? 8000 : 4000);
    }
  }
}

const application = Application.start();
application.register('toast', ToastController);
const notice = document.getElementById('notice');
if (notice) notice.dataset.controller = 'toast';
