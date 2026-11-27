/**
 * CivicBid founder onboarding
 *
 * Install this as a standalone Google Apps Script web app. Secrets belong in
 * Apps Script Properties, never in the spreadsheet or this source file.
 */

const SHEETS = {
  ONBOARDING: 'Onboarding',
  SUBSCRIBERS: 'Subscribers',
  SETTINGS: 'Read me',
};

const ONBOARDING_HEADERS = [
  'Created at', 'Request ID', 'Status', 'Company', 'Contact name', 'Email',
  'Phone', 'Website', 'Include terms', 'Regions', 'Exclusions', 'Consent at',
  'Razorpay subscription ID', 'Checkout URL', 'Last checked at', 'Error'
];

const SUBSCRIBER_HEADERS = [
  'ID', 'Email', 'Company name', 'Match all', 'Delivery mode', 'Include terms',
  'Exclude terms', 'Regions', 'Minimum score', 'Status',
  'Razorpay subscription ID', 'Updated at'
];

/** Creates the private operations spreadsheet and the hourly reconciliation trigger. */
function setupCivicBid() {
  const properties = PropertiesService.getScriptProperties();
  let spreadsheetId = properties.getProperty('SPREADSHEET_ID');
  let spreadsheet;
  if (spreadsheetId) {
    spreadsheet = SpreadsheetApp.openById(spreadsheetId);
  } else {
    spreadsheet = SpreadsheetApp.create('CivicBid – Operations');
    spreadsheetId = spreadsheet.getId();
    properties.setProperty('SPREADSHEET_ID', spreadsheetId);
  }

  initialiseSheet_(spreadsheet, SHEETS.ONBOARDING, ONBOARDING_HEADERS);
  initialiseSheet_(spreadsheet, SHEETS.SUBSCRIBERS, SUBSCRIBER_HEADERS);
  initialiseReadMe_(spreadsheet);
  if (!properties.getProperty('SYNC_TOKEN')) {
    properties.setProperty('SYNC_TOKEN', Utilities.getUuid().replace(/-/g, '') + Utilities.getUuid().replace(/-/g, ''));
  }
  installTriggers_();
  return {
    spreadsheetUrl: spreadsheet.getUrl(),
    syncToken: properties.getProperty('SYNC_TOKEN'),
    readyForPayment: paymentIsConfigured_(),
  };
}

function doGet(event) {
  const parameters = (event && event.parameter) || {};
  if (parameters.mode === 'subscribers') {
    return subscriberFeed_(parameters.token || '');
  }
  return HtmlService.createHtmlOutputFromFile('Index')
    .setTitle('CivicBid founder access');
}

/** Called by the public web form. It returns a Razorpay checkout URL only after saving the request. */
function submitApplication(form) {
  // Silent honeypot: normal visitors never see or fill this field.
  if (String((form || {}).companyUrl || '').trim()) {
    return { ok: true, paymentReady: false, message: 'Your request has been saved.' };
  }
  const application = validateApplication_(form || {});
  const lock = LockService.getScriptLock();
  lock.waitLock(20 * 1000);
  try {
    const onboarding = getSheet_(SHEETS.ONBOARDING);
    const values = onboarding.getDataRange().getValues();
    const emailColumn = ONBOARDING_HEADERS.indexOf('Email');
    const statusColumn = ONBOARDING_HEADERS.indexOf('Status');
    const duplicate = values.slice(1).some(row =>
      String(row[emailColumn]).toLowerCase() === application.email.toLowerCase() &&
      ['NEW', 'PAYMENT_LINK_SENT', 'PAYMENT_PENDING', 'ACTIVE'].includes(String(row[statusColumn]))
    );
    if (duplicate) {
      return { ok: false, message: 'An application already exists for this email. Please contact support if you need help.' };
    }

    const requestId = `civicbid-${Utilities.getUuid()}`;
    const now = new Date();
    const row = [
      now, requestId, 'NEW', application.company, application.contactName, application.email,
      application.phone, application.website, application.includeTerms.join(', '), application.regions.join(', '),
      application.exclusions.join(', '), now, '', '', '', ''
    ];
    onboarding.appendRow(row);
    const rowNumber = onboarding.getLastRow();

    if (!paymentIsConfigured_()) {
      setOnboardingCells_(onboarding, rowNumber, { Status: 'PAYMENT_CONFIGURATION_PENDING' });
      return { ok: true, paymentReady: false, message: 'Your request has been saved. Founder payments are opening shortly.' };
    }

    const subscription = createRazorpaySubscription_(application, requestId);
    setOnboardingCells_(onboarding, rowNumber, {
      Status: 'PAYMENT_LINK_SENT',
      'Razorpay subscription ID': subscription.id,
      'Checkout URL': subscription.short_url,
      'Last checked at': now,
    });
    return { ok: true, paymentReady: true, checkoutUrl: subscription.short_url };
  } catch (error) {
    console.error(error);
    return { ok: false, message: 'We could not start secure checkout. Please try again later.' };
  } finally {
    lock.releaseLock();
  }
}

/** Runs hourly. Only verified Razorpay subscription status can enable a profile. */
function reconcilePayments() {
  if (!paymentIsConfigured_()) return;
  const onboarding = getSheet_(SHEETS.ONBOARDING);
  const data = onboarding.getDataRange().getValues();
  const headerIndex = indexByHeader_(ONBOARDING_HEADERS);
  data.slice(1).forEach((row, offset) => {
    const rowNumber = offset + 2;
    const status = String(row[headerIndex.Status]);
    const subscriptionId = String(row[headerIndex['Razorpay subscription ID']] || '');
    if (!subscriptionId || !['PAYMENT_LINK_SENT', 'PAYMENT_PENDING', 'ACTIVE'].includes(status)) return;
    try {
      const subscription = razorpayRequest_(`/subscriptions/${encodeURIComponent(subscriptionId)}`, 'get');
      const update = { 'Last checked at': new Date() };
      if (subscription.status === 'active') {
        update.Status = 'ACTIVE';
        activateSubscriber_(row, headerIndex, subscriptionId);
      } else if (['halted', 'cancelled', 'completed', 'expired'].includes(subscription.status)) {
        update.Status = subscription.status.toUpperCase();
        deactivateSubscriber_(subscriptionId);
      } else {
        update.Status = 'PAYMENT_PENDING';
      }
      setOnboardingCells_(onboarding, rowNumber, update);
    } catch (error) {
      console.error(`Could not check ${subscriptionId}: ${error}`);
      setOnboardingCells_(onboarding, rowNumber, { 'Last checked at': new Date(), Error: 'Payment status check failed; will retry.' });
    }
  });
}

/** Returns only active delivery profiles to the GitHub Actions worker. */
function subscriberFeed_(providedToken) {
  const expected = PropertiesService.getScriptProperties().getProperty('SYNC_TOKEN') || '';
  if (!expected || providedToken !== expected) {
    return ContentService.createTextOutput(JSON.stringify({ error: 'unauthorised' }))
      .setMimeType(ContentService.MimeType.JSON);
  }
  const sheet = getSheet_(SHEETS.SUBSCRIBERS);
  const rows = sheet.getDataRange().getValues();
  const index = indexByHeader_(SUBSCRIBER_HEADERS);
  const subscribers = rows.slice(1)
    .filter(row => String(row[index.Status]).toLowerCase() === 'active')
    .map(row => ({
      id: String(row[index.ID]),
      email: String(row[index.Email]),
      company_name: String(row[index['Company name']]),
      match_all: String(row[index['Match all']]).toLowerCase() === 'true',
      delivery_mode: String(row[index['Delivery mode']]),
      include_terms: splitList_(row[index['Include terms']]),
      exclude_terms: splitList_(row[index['Exclude terms']]),
      regions: splitList_(row[index.Regions]),
      minimum_score: Number(row[index['Minimum score']]) || 0,
      status: 'active',
    }));
  return ContentService.createTextOutput(JSON.stringify(subscribers))
    .setMimeType(ContentService.MimeType.JSON);
}

function createRazorpaySubscription_(application, requestId) {
  const now = Math.floor(Date.now() / 1000);
  return razorpayRequest_('/subscriptions', 'post', {
    plan_id: PropertiesService.getScriptProperties().getProperty('RAZORPAY_PLAN_ID'),
    total_count: 120,
    quantity: 1,
    // The customer is redirected to short_url immediately. Do not make Razorpay
    // email payment links from an unauthenticated public form.
    customer_notify: false,
    expire_by: now + (7 * 24 * 60 * 60),
    notes: { civicbid_request_id: requestId, company: application.company },
  });
}

function razorpayRequest_(path, method, body) {
  const properties = PropertiesService.getScriptProperties();
  const keyId = properties.getProperty('RAZORPAY_KEY_ID');
  const keySecret = properties.getProperty('RAZORPAY_KEY_SECRET');
  const options = {
    method: method,
    muteHttpExceptions: true,
    headers: { Authorization: 'Basic ' + Utilities.base64Encode(`${keyId}:${keySecret}`) },
  };
  if (body) {
    options.contentType = 'application/json';
    options.payload = JSON.stringify(body);
  }
  const response = UrlFetchApp.fetch(`https://api.razorpay.com/v1${path}`, options);
  const code = response.getResponseCode();
  if (code < 200 || code >= 300) throw new Error(`Razorpay request failed (${code})`);
  return JSON.parse(response.getContentText());
}

function activateSubscriber_(onboardingRow, index, subscriptionId) {
  const subscribers = getSheet_(SHEETS.SUBSCRIBERS);
  const subscriberIndex = indexByHeader_(SUBSCRIBER_HEADERS);
  const email = String(onboardingRow[index.Email]).toLowerCase();
  const terms = splitList_(onboardingRow[index['Include terms']]);
  const regions = splitList_(onboardingRow[index.Regions]);
  const exclusions = splitList_(onboardingRow[index.Exclusions]);
  const profile = [
    `civicbid-${subscriptionId}`, email, onboardingRow[index.Company],
    String(!(terms.length || regions.length || exclusions.length)), 'daily_digest',
    terms.join(', '), exclusions.join(', '), regions.join(', '), 0, 'active', subscriptionId, new Date()
  ];
  const rows = subscribers.getDataRange().getValues();
  const existing = rows.findIndex((row, i) => i > 0 && String(row[subscriberIndex['Razorpay subscription ID']]) === subscriptionId);
  if (existing > 0) subscribers.getRange(existing + 1, 1, 1, profile.length).setValues([profile]);
  else subscribers.appendRow(profile);
}

function deactivateSubscriber_(subscriptionId) {
  const subscribers = getSheet_(SHEETS.SUBSCRIBERS);
  const rows = subscribers.getDataRange().getValues();
  const index = indexByHeader_(SUBSCRIBER_HEADERS);
  rows.slice(1).forEach((row, offset) => {
    if (String(row[index['Razorpay subscription ID']]) === subscriptionId) {
      setSubscriberStatus_(subscribers, offset + 2, 'inactive');
    }
  });
}

function validateApplication_(form) {
  const clean = value => String(value || '').trim();
  const split = value => clean(value).split(',').map(item => item.trim()).filter(Boolean);
  const application = {
    company: clean(form.company), contactName: clean(form.contactName), email: clean(form.email).toLowerCase(),
    phone: clean(form.phone), website: clean(form.website), includeTerms: split(form.includeTerms),
    regions: split(form.regions), exclusions: split(form.exclusions), consent: Boolean(form.consent),
  };
  if (!application.company || !application.contactName || !/^\S+@\S+\.\S+$/.test(application.email) || !application.phone || !application.consent) {
    throw new Error('Required fields are missing or invalid.');
  }
  if (!/^\+?[0-9 ()-]{7,25}$/.test(application.phone)) throw new Error('Invalid phone number.');
  return application;
}

function paymentIsConfigured_() {
  const properties = PropertiesService.getScriptProperties();
  return ['RAZORPAY_KEY_ID', 'RAZORPAY_KEY_SECRET', 'RAZORPAY_PLAN_ID'].every(key => Boolean(properties.getProperty(key)));
}

function installTriggers_() {
  ScriptApp.getProjectTriggers().filter(trigger => trigger.getHandlerFunction() === 'reconcilePayments')
    .forEach(trigger => ScriptApp.deleteTrigger(trigger));
  ScriptApp.newTrigger('reconcilePayments').timeBased().everyHours(1).create();
}

function initialiseSheet_(spreadsheet, name, headers) {
  const sheet = spreadsheet.getSheetByName(name) || spreadsheet.insertSheet(name);
  if (sheet.getLastRow() === 0) sheet.appendRow(headers);
  sheet.setFrozenRows(1);
  sheet.getRange(1, 1, 1, headers.length).setFontWeight('bold').setBackground('#d9e2ec');
  sheet.autoResizeColumns(1, headers.length);
}

function initialiseReadMe_(spreadsheet) {
  const sheet = spreadsheet.getSheetByName(SHEETS.SETTINGS) || spreadsheet.insertSheet(SHEETS.SETTINGS);
  sheet.clear();
  sheet.getRange(1, 1, 7, 1).setValues([
    ['CivicBid operations workbook'],
    ['Do not put API keys or passwords in this Sheet. Use Apps Script project settings → Script properties.'],
    ['The Onboarding tab is the audit trail. The Subscribers tab contains only profiles enabled after Razorpay confirms an active subscription.'],
    ['The hourly Apps Script trigger reconciles payment status. Google may schedule it approximately, not at an exact minute.'],
    ['To connect GitHub Actions later, deploy this script as a web app and add the web app URL plus SYNC_TOKEN as GitHub secrets.'],
    ['Required future Script Properties: RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET, RAZORPAY_PLAN_ID.'],
    ['Never share or publish the SYNC_TOKEN.'],
  ]);
  sheet.getRange('A1').setFontWeight('bold').setFontSize(14);
  sheet.setColumnWidth(1, 900);
  sheet.getRange('A1:A7').setWrap(true);
}

function getSheet_(name) {
  const spreadsheetId = PropertiesService.getScriptProperties().getProperty('SPREADSHEET_ID');
  if (!spreadsheetId) throw new Error('Run setupCivicBid first.');
  const sheet = SpreadsheetApp.openById(spreadsheetId).getSheetByName(name);
  if (!sheet) throw new Error(`Missing sheet: ${name}`);
  return sheet;
}

function indexByHeader_(headers) {
  return headers.reduce((result, header, index) => { result[header] = index; return result; }, {});
}

function setOnboardingCells_(sheet, rowNumber, values) {
  const index = indexByHeader_(ONBOARDING_HEADERS);
  Object.keys(values).forEach(header => sheet.getRange(rowNumber, index[header] + 1).setValue(values[header]));
}

function setSubscriberStatus_(sheet, rowNumber, status) {
  sheet.getRange(rowNumber, indexByHeader_(SUBSCRIBER_HEADERS).Status + 1).setValue(status);
}

function splitList_(value) {
  return String(value || '').split(',').map(item => item.trim()).filter(Boolean);
}
