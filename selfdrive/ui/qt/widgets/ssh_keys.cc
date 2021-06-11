#include "ssh_keys.h"

#include <QHBoxLayout>
#include <QNetworkReply>

#include "selfdrive/common/params.h"
#include "selfdrive/ui/qt/api.h"
#include "selfdrive/ui/qt/widgets/input.h"

SshControl::SshControl() : AbstractControl("SSH키", "Github 사용자 ID에 등록된 SSH키로 변경합니다.", "../assets/offroad/icon_ssh.png") {

  // setup widget
  hlayout->addStretch(1);

  username_label.setAlignment(Qt::AlignVCenter);
  username_label.setStyleSheet("color: #e0e879");
  hlayout->addWidget(&username_label);

  btn.setStyleSheet(R"(
    padding: 0;
    border-radius: 50px;
    font-size: 35px;
    font-weight: 500;
    color: #E4E4E4;
    background-color: #393939;
  )");
  btn.setFixedSize(250, 100);
  hlayout->addWidget(&btn);

  QObject::connect(&btn, &QPushButton::released, [=]() {
    if (btn.text() == "개인키사용") {
      QString username = InputDialog::getText("GitHub ID를 입력하세요");
      if (username.length() > 0) {
        btn.setText("로딩중");
        btn.setEnabled(false);
        getUserKeys(username);
      }
    } else {
      params.remove("GithubUsername");
      params.remove("GithubSshKeys");
      refresh();
    }
  });

  refresh();
}

void SshControl::refresh() {
  QString param = QString::fromStdString(params.get("GithubSshKeys"));
  std::string paramc = Params().get("GithubUsername");
  if (param.length() && !paramc.empty()) {
    username_label.setText(QString::fromStdString(params.get("GithubUsername")));
    btn.setText("개인키제거");
  } else if (param.length() && paramc.empty()) {
    username_label.setText("Public Key");
    btn.setText("공개키제거");
  } else {
    username_label.setText("");
    btn.setText("개인키사용");
  }
  btn.setEnabled(true);
}

void SshControl::getUserKeys(const QString &username) {
  HttpRequest *request = new HttpRequest(this, "https://github.com/" + username + ".keys", "", false);
  QObject::connect(request, &HttpRequest::receivedResponse, [=](const QString &resp) {
    if (!resp.isEmpty()) {
      Params params;
      params.put("GithubUsername", username.toStdString());
      params.put("GithubSshKeys", resp.toStdString());
    } else {
      ConfirmationDialog::alert(username + "등록된 SSH키가 없습니다.");
    }
    refresh();
    request->deleteLater();
  });
  QObject::connect(request, &HttpRequest::failedResponse, [=] {
    ConfirmationDialog::alert(username + "등록된 사용자가 아닙니다.");
    refresh();
    request->deleteLater();
  });
  QObject::connect(request, &HttpRequest::timeoutResponse, [=] {
    ConfirmationDialog::alert("요청시간이 초과되었습니다.");
    refresh();
    request->deleteLater();
  });
}

//LateralControlSelect
LateralControlSelect::LateralControlSelect() : AbstractControl("LateralControl [√]", "조향로직을 선택합니다. (PID/INDI/LQR)", "../assets/offroad/icon_logic.png") {
  label.setAlignment(Qt::AlignVCenter|Qt::AlignRight);
  label.setStyleSheet("color: #e0e879");
  hlayout->addWidget(&label);

  btnminus.setStyleSheet(R"(
    padding: 0;
    border-radius: 50px;
    font-size: 45px;
    font-weight: 500;
    color: #E4E4E4;
    background-color: #393939;
  )");
  btnplus.setStyleSheet(R"(
    padding: 0;
    border-radius: 50px;
    font-size: 45px;
    font-weight: 500;
    color: #E4E4E4;
    background-color: #393939;
  )");
  btnminus.setFixedSize(120, 100);
  btnplus.setFixedSize(120, 100);

  hlayout->addWidget(&btnminus);
  hlayout->addWidget(&btnplus);

  QObject::connect(&btnminus, &QPushButton::released, [=]() {
    auto str = QString::fromStdString(Params().get("LateralControlSelect"));
    int latcontrol = str.toInt();
    latcontrol = latcontrol - 1;
    if (latcontrol <= 0 ) {
      latcontrol = 0;
    } else {
    }
    QString latcontrols = QString::number(latcontrol);
    Params().put("LateralControlSelect", latcontrols.toStdString());
    refresh();
  });

  QObject::connect(&btnplus, &QPushButton::released, [=]() {
    auto str = QString::fromStdString(Params().get("LateralControlSelect"));
    int latcontrol = str.toInt();
    latcontrol = latcontrol + 1;
    if (latcontrol >= 2 ) {
      latcontrol = 2;
//    if (latcontrol >= 3 ) {
//      latcontrol = 3;
    } else {
    }
    QString latcontrols = QString::number(latcontrol);
    Params().put("LateralControlSelect", latcontrols.toStdString());
    refresh();
  });
  refresh();
}

void LateralControlSelect::refresh() {
  QString latcontrol = QString::fromStdString(Params().get("LateralControlSelect"));
  if (latcontrol == "0") {
    label.setText(QString::fromStdString("PID"));
  } else if (latcontrol == "1") {
    label.setText(QString::fromStdString("INDI"));
  } else if (latcontrol == "2") {
    label.setText(QString::fromStdString("LQR"));
//  } else if (latcontrol == "3") {
//    label.setText(QString::fromStdString("Kale"));
  }
  btnminus.setText("◀");
  btnplus.setText("▶");
}

//MfcSelect
MfcSelect::MfcSelect() : AbstractControl("MFC [√]", "MFC를 선택합니다. (LKAS/LDWS/LFA)", "../assets/offroad/icon_mfc.png") {
  label.setAlignment(Qt::AlignVCenter|Qt::AlignRight);
  label.setStyleSheet("color: #e0e879");
  hlayout->addWidget(&label);

  btnminus.setStyleSheet(R"(
    padding: 0;
    border-radius: 50px;
    font-size: 45px;
    font-weight: 500;
    color: #E4E4E4;
    background-color: #393939;
  )");
  btnplus.setStyleSheet(R"(
    padding: 0;
    border-radius: 50px;
    font-size: 45px;
    font-weight: 500;
    color: #E4E4E4;
    background-color: #393939;
  )");
  btnminus.setFixedSize(120, 100);
  btnplus.setFixedSize(120, 100);

  hlayout->addWidget(&btnminus);
  hlayout->addWidget(&btnplus);

  QObject::connect(&btnminus, &QPushButton::released, [=]() {
    auto str = QString::fromStdString(Params().get("MfcSelect"));
    int mfc = str.toInt();
    mfc = mfc - 1;
    if (mfc <= 0 ) {
      mfc = 0;
    } else {
    }
    QString mfcs = QString::number(mfc);
    Params().put("MfcSelect", mfcs.toStdString());
    refresh();
  });

  QObject::connect(&btnplus, &QPushButton::released, [=]() {
    auto str = QString::fromStdString(Params().get("MfcSelect"));
    int mfc = str.toInt();
    mfc = mfc + 1;
    if (mfc >= 2 ) {
      mfc = 2;
    } else {
    }
    QString mfcs = QString::number(mfc);
    Params().put("MfcSelect", mfcs.toStdString());
    refresh();
  });
  refresh();
}

void MfcSelect::refresh() {
  QString mfc = QString::fromStdString(Params().get("MfcSelect"));
  if (mfc == "0") {
    label.setText(QString::fromStdString("LKAS"));
  } else if (mfc == "1") {
    label.setText(QString::fromStdString("LDWS"));
  } else if (mfc == "2") {
    label.setText(QString::fromStdString("LFA"));
  }
  btnminus.setText("◀");
  btnplus.setText("▶");
}

//LongControlSelect
LongControlSelect::LongControlSelect() : AbstractControl("LongControl [√]", "LongControl 모드를 선택합니다. (MAD/LONG)", "../assets/offroad/icon_long.png") {
  label.setAlignment(Qt::AlignVCenter|Qt::AlignRight);
  label.setStyleSheet("color: #e0e879");
  hlayout->addWidget(&label);

  btnminus.setStyleSheet(R"(
    padding: 0;
    border-radius: 50px;
    font-size: 45px;
    font-weight: 500;
    color: #E4E4E4;
    background-color: #393939;
  )");
  btnplus.setStyleSheet(R"(
    padding: 0;
    border-radius: 50px;
    font-size: 45px;
    font-weight: 500;
    color: #E4E4E4;
    background-color: #393939;
  )");
  btnminus.setFixedSize(120, 100);
  btnplus.setFixedSize(120, 100);

  hlayout->addWidget(&btnminus);
  hlayout->addWidget(&btnplus);

  QObject::connect(&btnminus, &QPushButton::released, [=]() {
    auto str = QString::fromStdString(Params().get("LongControlSelect"));
    int longcontrol = str.toInt();
    longcontrol = longcontrol - 1;
    if (longcontrol <= 0 ) {
      longcontrol = 0;
    } else {
    }
    QString longcontrols = QString::number(longcontrol);
    Params().put("LongControlSelect", longcontrols.toStdString());
    refresh();
  });

  QObject::connect(&btnplus, &QPushButton::released, [=]() {
    auto str = QString::fromStdString(Params().get("LongControlSelect"));
    int longcontrol = str.toInt();
    longcontrol = longcontrol + 1;
    if (longcontrol >= 1 ) {
      longcontrol = 1;
    } else {
    }
    QString longcontrols = QString::number(longcontrol);
    Params().put("LongControlSelect", longcontrols.toStdString());
    refresh();
  });
  refresh();
}

void LongControlSelect::refresh() {
  QString longcontrol = QString::fromStdString(Params().get("LongControlSelect"));
  if (longcontrol == "0") {
    label.setText(QString::fromStdString("MAD"));
  } else if (longcontrol == "1") {
    label.setText(QString::fromStdString("LONG"));
  }
  btnminus.setText("◀");
  btnplus.setText("▶");
}
